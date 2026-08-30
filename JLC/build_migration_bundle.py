#!/usr/bin/env python3
"""Build a deterministic KiCad ZIP for JLCEDA Pro migration.

The bundle intentionally contains only the live KiCad project, library tables,
and project-local symbol/footprint/3D libraries.  Caches, backups and existing
Gerber/BOM production outputs are excluded so the importer sees one clear
source of truth.
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
    "SPINC AA Charger.kicad_pro",
    "SPINC AA Charger.kicad_sch",
    "SPINC AA Charger.kicad_pcb",
    "fp-lib-table",
    "sym-lib-table",
]
FIXED_ZIP_TIME = (2024, 10, 3, 0, 0, 0)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def collect_files() -> list[Path]:
    missing = [name for name in CORE if not (SOURCE / name).is_file()]
    if missing:
        raise SystemExit(f"missing required KiCad files: {', '.join(missing)}")

    files = [SOURCE / name for name in CORE]
    lib_root = SOURCE / "project_libraries"
    if not lib_root.is_dir():
        raise SystemExit("missing project_libraries directory")

    for path in lib_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.endswith(".bak") or path.name.startswith("."):
            continue
        files.append(path)
    return sorted(set(files), key=lambda p: p.relative_to(SOURCE).as_posix())


def run_reproduction_guardrails() -> None:
    """Keep the migration artifact downstream of every golden-Rev-A gate."""
    for verifier in ("verify_rev_a.py", "verify_power_stage.py"):
        subprocess.run([sys.executable, str(ROOT / "JLC" / verifier)], check=True)


def build(output: Path) -> dict[str, object]:
    # Refuse to package a source tree that no longer passes the reproduction
    # guardrails.  This keeps the migration ZIP downstream of the same SSoT.
    run_reproduction_guardrails()

    members = collect_files()
    manifest_files: list[dict[str, object]] = []
    payloads: list[tuple[str, bytes]] = []
    for path in members:
        arcname = path.relative_to(SOURCE).as_posix()
        data = path.read_bytes()
        payloads.append((arcname, data))
        manifest_files.append({"path": arcname, "bytes": len(data), "sha256": sha256(data)})

    manifest = {
        "schema": 1,
        "name": "SPINC JLC Rev A KiCad migration bundle",
        "source_repository": "lcolok/SPINC",
        "source_branch": "jlc-rev-a",
        "source_commit": git_head(),
        "upstream_golden_commit": "af7b36e8ca5e99bfb3e99d8b02d9864117091de7",
        "target": "JLCEDA Pro SYS_FileManager.importProjectByProjectFile(fileType=KiCad)",
        "files": manifest_files,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for arcname, data in payloads:
            info = zipfile.ZipInfo(arcname, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo("JLC-MIGRATION-MANIFEST.json", FIXED_ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest_data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    # Reopen and assert the archive contains the minimum importer contract.
    with zipfile.ZipFile(output, "r") as archive:
        names = archive.namelist()
        if not any(name.lower().endswith(".kicad_sch") for name in names):
            raise SystemExit("generated archive is missing .kicad_sch")
        if not any(name.lower().endswith(".kicad_pcb") for name in names):
            raise SystemExit("generated archive is missing .kicad_pcb")
        bad = archive.testzip()
        if bad is not None:
            raise SystemExit(f"generated ZIP CRC failure: {bad}")

    return {
        "output": str(output),
        "bytes": output.stat().st_size,
        "sha256": sha256(output.read_bytes()),
        "members": len(payloads) + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = build(args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
