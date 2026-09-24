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
import tempfile
import zipfile
from pathlib import Path

from kicad_zone_split import TRANSFORM as ZONE_SPLIT, split_multilayer_zones

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
        for scope in ([], ["--cached"]):
            subprocess.run(["git", "-C", str(ROOT), "diff", *scope, "--no-ext-diff", "--no-textconv", "--quiet", "HEAD", "--"],
                           check=True, stderr=subprocess.DEVNULL)
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


def validate_output(output: Path) -> Path:
    """Refuse input/metadata aliases before opening or replacing any output."""
    if output.is_symlink():
        raise SystemExit("refusing output: symbolic links are not supported")
    destination = output.resolve()
    protected = [SOURCE.resolve(), (ROOT / ".git").resolve()]
    for flag in ("--absolute-git-dir", "--git-common-dir"):
        value = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", flag], text=True).strip()
        path = Path(value)
        protected.append((path if path.is_absolute() else ROOT / path).resolve())
    if any(destination == path or destination.is_relative_to(path) for path in protected):
        raise SystemExit("refusing output: source or Git metadata path")
    if destination.exists() and (not destination.is_file() or destination.stat().st_nlink != 1):
        raise SystemExit("refusing output: must be a regular file without hard-link aliases")
    if destination.is_relative_to(ROOT.resolve()):
        relative = destination.relative_to(ROOT.resolve()).as_posix()
        result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", relative],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            raise SystemExit("refusing output: cannot overwrite a tracked file")
        if result.returncode != 1:
            raise SystemExit("refusing output: cannot determine tracked-file ownership")
    return destination


def committed_payload(path: Path, commit: str) -> bytes:
    """Git status alone can miss assume-unchanged files and concurrent edits."""
    relative = path.relative_to(ROOT).as_posix()
    for candidate in (path, *path.parents):
        if candidate == ROOT:
            break
        if candidate.is_symlink():
            raise SystemExit(f"refusing symlink migration input: {relative}")
    entry = subprocess.check_output(["git", "-C", str(ROOT), "ls-tree", "-z", commit, "--", relative])
    fields = entry.split(b"\t", 1)[0].split()
    if len(fields) != 3 or fields[0] not in (b"100644", b"100755") or fields[1] != b"blob":
        raise SystemExit(f"refusing untracked or non-regular migration input: {relative}")
    expected = subprocess.check_output(["git", "-C", str(ROOT), "cat-file", "blob", fields[2].decode("ascii")])
    if path.read_bytes() != expected:
        raise SystemExit(f"refusing migration input not matching source commit: {relative}")
    return expected


def build(output: Path) -> dict[str, object]:
    source_commit = git_head()
    output = validate_output(output)
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
        data = committed_payload(path, source_commit)
        entry: dict[str, object] = {"path": arcname}
        if arcname.endswith(".kicad_pcb"):
            # JLCEDA gives every per-layer pour of a multi-layer zone all of
            # the zone's cached fills; import equivalent single-layer zones.
            try:
                text, report = split_multilayer_zones(data.decode("utf-8"))
            except ValueError as exc:
                raise SystemExit(f"refusing {arcname}: {ZONE_SPLIT} failed closed: {exc}")
            entry.update(source_bytes=len(data), source_sha256=sha256(data),
                         transform=ZONE_SPLIT, transform_report=report)
            data = text.encode("utf-8")
        entry.update(bytes=len(data), sha256=sha256(data))
        payloads.append((arcname, data))
        manifest_files.append(entry)
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
    # Publish only a complete, checked archive. A failure must not truncate an
    # existing artifact; consumers must still check this invocation's exit code.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False) as handle:
        pending = Path(handle.name)
    try:
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for arcname, data in [*payloads, ("JLC-MIGRATION-MANIFEST.json", manifest_data)]:
                info = zipfile.ZipInfo(arcname, FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        with zipfile.ZipFile(pending, "r") as archive:
            names = archive.namelist()
            if not any(name.lower().endswith(".kicad_sch") for name in names):
                raise SystemExit("generated archive is missing .kicad_sch")
            if not any(name.lower().endswith(".kicad_pcb") for name in names):
                raise SystemExit("generated archive is missing .kicad_pcb")
            bad = archive.testzip()
            if bad is not None:
                raise SystemExit(f"generated ZIP CRC failure: {bad}")
        if git_head() != source_commit or collect_files() != members:
            raise SystemExit("refusing bundle: source changed during build")
        if validate_output(output) != output:
            raise SystemExit("refusing output: destination changed during build")
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return {"output": str(output), "bytes": output.stat().st_size,
            "sha256": sha256(output.read_bytes()), "members": len(payloads) + 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
