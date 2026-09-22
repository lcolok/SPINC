"""Regressions for the offline local acceptance entry point; synthetic data only."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import verify_local as local


class SummaryTests(unittest.TestCase):
    def test_nonempty_audit(self):
        self.assertEqual(local.audit_summary("SPINC: 492 checks passed, 0 failed")["checks_passed"], 492)

    def test_bad_audits_are_not_passes(self):
        for output in ("", "OK", "0 checks passed, 0 failed", "491 checks passed, 1 failed"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                local.audit_summary(output)

    def test_nonempty_unittest(self):
        self.assertEqual(local.test_summary("Ran 55 tests in 0.3s\n\nOK\n")["tests_run"], 55)
        self.assertEqual(local.test_summary("Ran 1 test in 0.3s\nOK\n")["tests_run"], 1)

    def test_empty_discovery_is_not_pass(self):
        with self.assertRaises(ValueError):
            local.test_summary("Ran 0 tests in 0.0s\nOK\n")

    def test_skipped_is_not_full_acceptance(self):
        with self.assertRaises(ValueError):
            local.test_summary("Ran 3 tests in 0.1s\nOK (skipped=1)\n")

    def test_failed_or_missing_summary(self):
        for output in ("", "test_one ... ok", "Ran 2 tests in 0.1s\nFAILED (failures=1)\n"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                local.test_summary(output)


class StepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def step(self, code: str, **kwargs):
        return local.run_step("fixture", [sys.executable, "-c", code], self.root, root=self.root, **kwargs)

    def test_success_and_log(self):
        result = self.step("print('hello')")
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello", (self.root / "fixture.log").read_text())

    def test_nonzero_even_with_pass_text(self):
        result = self.step("print('PASS'); raise SystemExit(7)")
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["exit_code"], 7)

    def test_zero_exit_with_empty_discovery_is_failure(self):
        result = self.step("print('Ran 0 tests in 0.0s\\nOK')", validator=local.test_summary)
        self.assertEqual(result["result"], "failed")
        self.assertIn("zero tests", result["error"])

    def test_missing_executable(self):
        result = local.run_step("missing", [str(self.root / "absent")], self.root, root=self.root)
        self.assertEqual(result["result"], "failed")
        self.assertTrue((self.root / "missing.log").exists())

    def test_timeout(self):
        result = self.step("import time; time.sleep(2)", timeout=0.02)
        self.assertEqual(result["result"], "failed")
        self.assertIn("timed out", result["error"])


class BundleIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = "a" * 40
        self.data = b"synthetic KiCad payload"
        self.manifest = {"source_commit": self.commit,
                         "files": [{"path": "fixture.kicad_pcb", "bytes": len(self.data),
                                    "sha256": local.digest(self.data)}]}

    def pair(self, *, payload=None, extra=False):
        one, two = self.root / "one.zip", self.root / "two.zip"
        with zipfile.ZipFile(one, "w") as archive:
            archive.writestr("JLC-MIGRATION-MANIFEST.json", json.dumps(self.manifest))
            archive.writestr("fixture.kicad_pcb", self.data if payload is None else payload)
            if extra:
                archive.writestr("unmanifested.txt", "extra")
        two.write_bytes(one.read_bytes())
        return one, two

    def test_valid_byte_identical_payload(self):
        result = local.verify_bundle_pair(*self.pair(), self.commit)
        self.assertEqual(result["payload_files"], 1)
        self.assertTrue(result["byte_identical"])
        self.assertTrue(result["manifest_hashes_verified"])

    def test_nonidentical_bytes(self):
        first, second = self.pair()
        second.write_bytes(second.read_bytes() + b"different")
        with self.assertRaisesRegex(ValueError, "byte-identical"):
            local.verify_bundle_pair(first, second, self.commit)

    def test_wrong_commit(self):
        with self.assertRaisesRegex(ValueError, "immutable"):
            local.verify_bundle_pair(*self.pair(), "b" * 40)

    def test_branch_provenance_rejected(self):
        self.manifest["source_branch"] = "jlc-rev-a"
        with self.assertRaisesRegex(ValueError, "immutable"):
            local.verify_bundle_pair(*self.pair(), self.commit)

    def test_wrong_payload(self):
        with self.assertRaisesRegex(ValueError, "integrity"):
            local.verify_bundle_pair(*self.pair(payload=b"changed"), self.commit)

    def test_extra_member(self):
        with self.assertRaisesRegex(ValueError, "inventory"):
            local.verify_bundle_pair(*self.pair(extra=True), self.commit)

    def test_empty_manifest(self):
        self.manifest["files"] = []
        with self.assertRaisesRegex(ValueError, "empty"):
            local.verify_bundle_pair(*self.pair(), self.commit)

    def test_duplicate_manifest(self):
        self.manifest["files"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            local.verify_bundle_pair(*self.pair(), self.commit)


class EvidenceTests(unittest.TestCase):
    def test_report_replaces_state_without_pending_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            local.save_report(path, {"result": "running"})
            local.save_report(path, {"result": "failed"})
            self.assertEqual(json.loads((path / "summary.json").read_text())["result"], "failed")
            self.assertFalse((path / "summary.pending.json").exists())

    def test_missing_git_produces_fresh_failed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(local, "ROOT", root), patch.object(local, "source_identity", side_effect=RuntimeError("missing Git")), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(local.main([]), 1)
                self.assertEqual(local.main([]), 1)
            reports = sorted(root.glob("JLC/out/local-verification/*/summary.json"))
            self.assertEqual(len(reports), 2)
            for report in reports:
                data = json.loads(report.read_text())
                self.assertEqual(data["result"], "failed")
                self.assertIn("github-required-check", data["not_evaluated"])
                self.assertTrue((report.parent / "SHA256SUMS").is_file())


class ValidationInputTests(unittest.TestCase):
    def test_firmware_and_reproduction_contracts_are_stability_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "JLC/check.py",
                "JLC/reproduction-kit.json",
                "Platformio/check.py",
                "Platformio/platformio.ini",
                "Platformio/firmware-build-contract.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n")
            actual = {path.relative_to(root).as_posix() for path in local.verification_inputs(root)}
            self.assertEqual(actual, {
                "JLC/check.py",
                "JLC/reproduction-kit.json",
                "Platformio/check.py",
                "Platformio/platformio.ini",
                "Platformio/firmware-build-contract.json",
            })


class SourceSnapshotTests(unittest.TestCase):
    def test_same_dirty_status_still_detects_non_python_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for args in (("init", "-q"), ("config", "user.email", "fixture@example.invalid"),
                         ("config", "user.name", "Fixture")):
                subprocess.run(["git", "-C", str(root), *args], check=True)
            source = root / "flow.yaml"
            source.write_text("baseline\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
            source.write_text("working edit one\n")
            first = local.source_identity(root)
            source.write_text("working edit two\n")
            second = local.source_identity(root)
            self.assertEqual(first["status"], second["status"])
            self.assertNotEqual(first, second, "same M status cannot hide changed validation input")


if __name__ == "__main__":
    unittest.main()
