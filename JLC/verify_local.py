#!/usr/bin/env python3
"""Run offline SPINC source and static firmware checks; never run a live flow, merge, or order.

Each invocation creates a new ignored JLC/out/local-verification directory.
Full mode also checks two deterministic bundles from a clean tracked source.
Firmware runtime/build execution stays explicit in Platformio/golden_build.py.
Use --checks-only while editing. Neither mode satisfies a GitHub required check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION = "local-source-only-not-ci-manufacturing-or-physical-golden"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_summary(output: str) -> dict:
    match = re.search(r"(\d+) checks passed, (\d+) failed", output)
    if not match or int(match[1]) == 0 or int(match[2]) != 0:
        raise ValueError("missing, empty or failed audit summary")
    return {"checks_passed": int(match[1]), "checks_failed": int(match[2])}


def test_summary(output: str) -> dict:
    match = re.search(r"^Ran (\d+) tests? in ", output, re.MULTILINE)
    skipped = re.search(r"\bskipped=(\d+)\b", output)
    if not match or int(match[1]) == 0:
        raise ValueError("zero tests or missing unittest discovery summary")
    if skipped and int(skipped[1]) > 0:
        raise ValueError("skipped tests are not full local acceptance")
    if not re.search(r"^OK\s*$", output, re.MULTILINE):
        raise ValueError("unittest did not report an unqualified OK")
    return {"tests_run": int(match[1]), "tests_skipped": 0}


def source_identity(root: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    diffs = []
    for scope in ([], ["--cached"]):
        diffs.append(subprocess.check_output([
            "git", "-C", str(root), "diff", *scope, "--no-ext-diff", "--no-textconv",
            "--binary", "--no-color", "HEAD", "--"]))
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "tracked_dirty": any(diffs),
            "worktree_diff_sha256": digest(diffs[0]), "index_diff_sha256": digest(diffs[1]),
            "status": git("status", "--porcelain=v1", "--untracked-files=all")}


def verification_inputs(root: Path) -> list[Path]:
    """Inputs whose bytes must remain stable for one local acceptance run."""
    paths = [
        *sorted((root / "JLC").glob("*.py")),
        *sorted((root / "Platformio").glob("*.py")),
        root / "JLC/reproduction-kit.json",
        root / "Platformio/platformio.ini",
        root / "Platformio/firmware-build-contract.json",
    ]
    return sorted({path for path in paths if path.is_file()})


def run_step(name: str, command: list[str], directory: Path, *,
             validator: Callable[[str], dict] | None = None,
             timeout: float = 180, root: Path = ROOT) -> dict:
    started = time.monotonic()
    record = {"name": name, "command": command, "result": "failed", "log": f"{name}.log"}
    output = ""
    try:
        process = subprocess.run(command, cwd=root, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, timeout=timeout,
                                 env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        output = process.stdout
        record["exit_code"] = process.returncode
        if process.returncode != 0:
            raise RuntimeError(f"command exited {process.returncode}")
        if validator:
            record.update(validator(output))
        record["result"] = "passed"
    except subprocess.TimeoutExpired as exc:
        partial = exc.output or ""
        output = partial.decode("utf-8", errors="replace") if isinstance(partial, bytes) else partial
        record["error"] = f"timed out after {timeout} seconds"
    except (OSError, RuntimeError, ValueError) as exc:
        record["error"] = str(exc)
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    (directory / record["log"]).write_text(output + ("\nERROR: " + record["error"] if "error" in record else ""), encoding="utf-8")
    return record


def verify_bundle_pair(first: Path, second: Path, commit: str) -> dict:
    first_data, second_data = first.read_bytes(), second.read_bytes()
    if first_data != second_data:
        raise ValueError("repeated migration builds are not byte-identical")
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("duplicate ZIP members or CRC failure")
        manifest = json.loads(archive.read("JLC-MIGRATION-MANIFEST.json"))
        if manifest.get("source_commit") != commit or "source_branch" in manifest:
            raise ValueError("bundle does not identify the actual immutable source commit")
        members = manifest["files"]
        declared = [member["path"] for member in members]
        if not declared or len(set(declared)) != len(declared):
            raise ValueError("empty or duplicate manifest payload")
        if set(names) != set(declared) | {"JLC-MIGRATION-MANIFEST.json"}:
            raise ValueError("ZIP inventory and manifest disagree")
        for member in members:
            data = archive.read(member["path"])
            if len(data) != member["bytes"] or digest(data) != member["sha256"]:
                raise ValueError(f"payload integrity mismatch: {member['path']}")
    return {"sha256": digest(first_data), "bytes": len(first_data),
            "payload_files": len(members), "byte_identical": True,
            "crc_verified": True, "manifest_hashes_verified": True,
            "source_commit": commit}


def save_report(directory: Path, report: dict) -> None:
    pending = directory / "summary.pending.json"
    pending.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(directory / "summary.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checks-only", action="store_true", help="run working-tree checks without a commit-bound bundle")
    args = parser.parse_args(argv)
    output_root = ROOT / "JLC/out/local-verification"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(tempfile.mkdtemp(prefix=stamp + "-", dir=output_root))
    report = {"schema": 1, "qualification": QUALIFICATION, "started_at_utc": stamp,
              "mode": "checks-only" if args.checks_only else "checks-and-bundle",
              "result": "running", "workspace": str(ROOT), "steps": [],
              "python": platform.python_version(),
              "not_evaluated": ["github-required-check", "pinned-harness-loader",
                                "firmware-runtime", "remote-firmware-build",
                                "manufacturing", "physical-golden", "durable-off-machine-archive"]}
    save_report(directory, report)
    print(f"Evidence: {directory}", flush=True)
    try:
        report["source_start"] = source_identity(ROOT)
        inputs = verification_inputs(ROOT)
        report["validation_input_sha256"] = {
            p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in inputs
        }
        python = [sys.executable, "-B"]
        steps = [
            ("node-version", ["node", "--version"], None),
            ("rev-a", python + ["JLC/verify_rev_a.py"], audit_summary),
            ("power-stage", python + ["JLC/verify_power_stage.py"], audit_summary),
            ("bindings", python + ["JLC/build_jlc_binding_manifest.py", "--check"], None),
            ("reproduction-kit", python + ["JLC/verify_reproduction_kit.py"], None),
            ("firmware-contract", python + ["Platformio/verify_firmware_contract.py"], None),
            ("harness-pin", python + ["JLC/verify_harness_pin.py"], None),
            ("roundtrip", python + ["JLC/verify_jlc_export.py", "--self-test", "--report", str(directory / "roundtrip.json")], None),
            ("tests", python + ["-m", "unittest", "discover", "-s", "JLC", "-p", "test_*.py", "-v"], test_summary),
            ("firmware-tests", python + ["-m", "unittest", "discover", "-s", "Platformio", "-p", "test_*.py", "-v"], test_summary),
        ]
        scripts = ("import-rev-a-kicad", "audit-jlc-library-bindings", "export-rev-a-audit")
        steps.extend((f"syntax-{name}", ["node", "--check", f"JLC/jlceda-scripts/{name}.js"], None) for name in scripts)
        if not args.checks_only:
            for name in ("first", "repeat"):
                steps.append((f"bundle-{name}", python + ["JLC/build_migration_bundle.py", "--output", str(directory / f"bundle-{name}.zip")], None))
        for name, command, validator in steps:
            record = run_step(name, command, directory, validator=validator)
            report["steps"].append(record)
            save_report(directory, report)
            print(f"[{record['result'].upper()}] {name}" + (f": {record['error']}" if "error" in record else ""), flush=True)
            if record["result"] != "passed":
                raise RuntimeError(f"{name} failed; inspect {record['log']}")
        report["source_end"] = source_identity(ROOT)
        if report["source_start"] != report["source_end"]:
            raise RuntimeError("Git state changed during validation; rerun on a stable source")
        current_hashes = {
            p.relative_to(ROOT).as_posix(): digest(p.read_bytes())
            for p in verification_inputs(ROOT)
        }
        if current_hashes != report["validation_input_sha256"]:
            raise RuntimeError("validation inputs changed during this run")
        if not args.checks_only:
            if report["source_start"]["tracked_dirty"]:
                raise RuntimeError("dirty source cannot produce commit-bound acceptance")
            report["bundle"] = verify_bundle_pair(directory / "bundle-first.zip", directory / "bundle-repeat.zip", report["source_start"]["commit"])
        report["result"] = "checks-passed" if args.checks_only else "checks-and-bundle-passed"
        return_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        report["result"] = "failed"
        report["error"] = str(exc) or type(exc).__name__
        return_code = 1
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_report(directory, report)
    checksums = [f"{digest(p.read_bytes())}  {p.name}" for p in sorted(directory.iterdir()) if p.is_file() and p.name != "SHA256SUMS"]
    (directory / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(f"Result: {report['result']}; {QUALIFICATION}")
    if "error" in report:
        print(f"Error: {report['error']}")
    print(f"Report: {directory / 'summary.json'}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
