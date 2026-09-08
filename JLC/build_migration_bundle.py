#!/usr/bin/env python3
"""Build a deterministic KiCad ZIP for JLCEDA Pro migration.

Only the live KiCad project, library tables and project-local libraries are
included. Caches, backups and existing production outputs are excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "PCB" / "SPINC AA Charger"
OUT_DIR = ROOT / "JLC" / "out"
DEFAULT_OUT = OUT_DIR / "SPINC-JLC-Rev-A-KiCad.zip"
CORE = [
    "SPINC AA Charger.kicad_pro", "SPINC AA Charger.kicad_sch",
    "SPINC AA Charger.kicad_pcb", "fp-lib-table", "sym-lib-table",
]
FIXED_ZIP_TIME = (2024, 10, 3, 0, 0, 0)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_head() -> str:
    """A production bundle must identify a clean committed source, including PR merges."""
    try:
        subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--"], check=True, stderr=subprocess.DEVNULL)
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD^{commit}"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("refusing bundle: source must be a clean Git checkout with a commit") from exc


def collect_files() -> list[Path]:
    missing = [name for name in CORE if not (SOURCE / name).is_file()]
    if missing:
        raise SystemExit(f"missing required KiCad files: {', '.join(missing)}")
    files = [SOURCE / name for name in CORE]
    lib_root = SOURCE / "project_libraries"
    if not lib_root.is_dir():
        raise SystemExit("missing project_libraries directory")
    for path in lib_root.rglob("*"):
        if path.is_file() and not path.name.endswith(".bak") and not path.name.startswith("."):
            files.append(path)
    return sorted(set(files), key=lambda p: p.relative_to(SOURCE).as_posix())


def run_reproduction_guardrails() -> None:
    for verifier in ("verify_rev_a.py", "verify_power_stage.py"):
        subprocess.run([sys.executable, str(ROOT / "JLC" / verifier)], check=True)


def build(output: Path) -> dict[str, object]:
    source_commit = git_head()
    run_reproduction_guardrails()
    members = collect_files()
    manifest_files: list[dict[str, object]] = []
    payloads: list[tuple[str, bytes]] = []
    for path in members:
        # Do not accidentally package untracked or ignored local library files.
        relative = path.relative_to(ROOT).as_posix()
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", relative],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if tracked.returncode:
            raise SystemExit(f"refusing untracked migration input: {relative}")
        arcname = path.relative_to(SOURCE).as_posix()
        data = path.read_bytes()
        payloads.append((arcname, data))
        manifest_files.append({"path": arcname, "bytes": len(data), "sha256": sha256(data)})
    manifest = {
        "schema": 1,
        "name": "SPINC JLC Rev A KiCad migration bundle",
        "source_repository": "lcolok/SPINC",
        # A branch name is mutable and may be absent for PR or tag checkouts.
        # The exact commit is authoritative; never hard-code jlc-rev-a here.
        "source_commit": source_commit,
        "upstream_golden_commit": "af7b36e8ca5e99bfb3e99d8b02d9864117091de7",
        "target": "JLCEDA Pro SYS_FileManager.importProjectByProjectFile(fileType=KiCad)",
        "files": manifest_files,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for arcname, data in [*payloads, ("JLC-MIGRATION-MANIFEST.json", manifest_data)]:
            info = zipfile.ZipInfo(arcname, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(output, "r") as archive:
        names = archive.namelist()
        if not any(name.lower().endswith(".kicad_sch") for name in names):
            raise SystemExit("generated archive is missing .kicad_sch")
        if not any(name.lower().endswith(".kicad_pcb") for name in names):
            raise SystemExit("generated archive is missing .kicad_pcb")
        bad = archive.testzip()
        if bad is not None:
            raise SystemExit(f"generated ZIP CRC failure: {bad}")
    return {"output": str(output), "bytes": output.stat().st_size,
            "sha256": sha256(output.read_bytes()), "members": len(payloads) + 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
