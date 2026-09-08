"""Offline regression tests for source provenance and the harness pin contract."""
from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import build_migration_bundle as bundle
from verify_harness_pin import ROOT, validate_pin


class HarnessPinTests(unittest.TestCase):
    def setUp(self):
        self.pin = json.loads((ROOT / "JLC/harness-pin.json").read_text())

    def test_committed_pin(self):
        self.assertEqual(validate_pin(self.pin)["commit"], self.pin["commit"])

    def test_reject_wrong_repository(self):
        self.pin["repository"] = "someone/other"
        with self.assertRaises(ValueError):
            validate_pin(self.pin)

    def test_reject_non_immutable_or_injected_sha(self):
        for value in ("main", "197c8ee", "0" * 40, "a" * 40 + "\ninjected=yes", None):
            with self.subTest(value=value):
                pin = copy.deepcopy(self.pin)
                pin["commit"] = value
                with self.assertRaises(ValueError):
                    validate_pin(pin)

    def test_reject_missing_or_duplicate_capability(self):
        for caps in ([], self.pin["requiredCapabilities"] * 2, [False]):
            pin = copy.deepcopy(self.pin)
            pin["requiredCapabilities"] = caps
            with self.assertRaises(ValueError):
                validate_pin(pin)

    def test_reject_wrong_flow_or_schema(self):
        for key, value in (("flowSpec", "../other.yaml"), ("schema", True),
                           ("schema", 2), ("validationEntryPoint", "jlc flow run")):
            pin = copy.deepcopy(self.pin)
            pin[key] = value
            with self.assertRaises(ValueError):
                validate_pin(pin)


class BundleProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Test Fixture")
        (self.root / "source").write_text("source\n")
        self.git("add", "source")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def test_clean_checkout_has_exact_commit(self):
        with patch.object(bundle, "ROOT", self.root):
            self.assertEqual(bundle.git_head(), self.git("rev-parse", "HEAD"))

    def test_detached_checkout_has_exact_commit(self):
        self.git("checkout", "--detach", "-q")
        with patch.object(bundle, "ROOT", self.root):
            self.assertEqual(bundle.git_head(), self.git("rev-parse", "HEAD"))

    def test_dirty_tracked_source_fails_closed(self):
        (self.root / "source").write_text("changed\n")
        with patch.object(bundle, "ROOT", self.root), self.assertRaises(SystemExit):
            bundle.git_head()

    def test_missing_repository_fails_closed(self):
        with tempfile.TemporaryDirectory() as empty:
            with patch.object(bundle, "ROOT", Path(empty)), self.assertRaises(SystemExit):
                bundle.git_head()


class BundleBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "PCB" / "SPINC AA Charger"
        self.source.mkdir(parents=True)
        for name in bundle.CORE:
            (self.source / name).write_text("fixture: " + name)
        lib = self.source / "project_libraries"
        lib.mkdir()
        (lib / "fixture.kicad_sym").write_text("fixture symbol")
        for args in (("init", "-q"), ("config", "user.email", "fixture@example.invalid"),
                     ("config", "user.name", "Test Fixture"), ("add", "."),
                     ("commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True)
        self.addCleanup(patch.stopall)
        patch.object(bundle, "ROOT", self.root).start()
        patch.object(bundle, "SOURCE", self.source).start()
        # These synthetic fixtures exercise packaging, not electrical validation.
        patch.object(bundle, "run_reproduction_guardrails", lambda: None).start()

    def test_reproducible_bundle_and_exact_manifest(self):
        first, second = self.root / "one.zip", self.root / "two.zip"
        self.assertEqual(bundle.build(first)["sha256"], bundle.build(second)["sha256"])
        with zipfile.ZipFile(first) as archive:
            manifest = json.loads(archive.read("JLC-MIGRATION-MANIFEST.json"))
            self.assertEqual(manifest["source_commit"], bundle.git_head())
            self.assertNotIn("source_branch", manifest)
            for member in manifest["files"]:
                self.assertEqual(member["sha256"], bundle.sha256(archive.read(member["path"])))

    def test_untracked_library_fails_closed(self):
        (self.source / "project_libraries" / "untracked.kicad_sym").write_text("not committed")
        with self.assertRaisesRegex(SystemExit, "untracked"):
            bundle.build(self.root / "rejected.zip")

    def test_missing_core_file_fails_closed(self):
        (self.source / bundle.CORE[0]).unlink()
        with self.assertRaises(SystemExit):
            bundle.build(self.root / "rejected.zip")


if __name__ == "__main__":
    unittest.main()
