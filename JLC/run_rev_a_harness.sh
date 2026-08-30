#!/usr/bin/env bash
set -euo pipefail

# SPINC JLC Rev A — fail-closed real-runtime migration/evidence runner.
#
# This is the primary automated path once the mature `jlc` CLI is connected to
# a logged-in JLCEDA Pro runtime. It deliberately performs no design mutation or
# optimization beyond JLCEDA's KiCad import itself.
#
# Usage:
#   bash JLC/run_rev_a_harness.sh
#   bash JLC/run_rev_a_harness.sh /path/to/SPINC-JLC-Rev-A-KiCad.zip
#
# Environment overrides:
#   JLC_BIN=jlc
#   JLC_REV_A_PROJECT=SPINC-JLC-Rev-A
#   JLC_REV_A_OUT=JLC/out/runtime

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

JLC_BIN="${JLC_BIN:-jlc}"
PROJECT_NAME="${JLC_REV_A_PROJECT:-SPINC-JLC-Rev-A}"
OUT_DIR="${JLC_REV_A_OUT:-$ROOT/JLC/out/runtime}"
DEFAULT_BUNDLE="$ROOT/JLC/out/SPINC-JLC-Rev-A-KiCad.zip"
BUNDLE="${1:-$DEFAULT_BUNDLE}"
LOG="$OUT_DIR/harness.log"

mkdir -p "$OUT_DIR"
: > "$LOG"

stamp() {
  printf '[SPINC Rev A] %s\n' "$*" | tee -a "$LOG"
}

run_logged() {
  stamp "+ $*"
  "$@" 2>&1 | tee -a "$LOG"
}

fail() {
  printf '[SPINC Rev A] FAIL: %s\n' "$*" | tee -a "$LOG" >&2
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v "$JLC_BIN" >/dev/null 2>&1 || fail "JLC CLI not found: $JLC_BIN"

stamp "Phase 0/5 — verify frozen KiCad golden source"
run_logged python3 JLC/verify_rev_a.py
run_logged python3 JLC/verify_power_stage.py
run_logged python3 JLC/build_jlc_binding_manifest.py --check

if [[ $# -eq 0 ]]; then
  stamp "Building deterministic migration bundle from the verified golden source"
  run_logged python3 JLC/build_migration_bundle.py --output "$DEFAULT_BUNDLE"
fi

[[ -f "$BUNDLE" ]] || fail "migration bundle not found: $BUNDLE"
[[ -s "$BUNDLE" ]] || fail "migration bundle is empty: $BUNDLE"

# Feature preflight: do not start a cloud import with an older harness that
# cannot produce the CPL required for round-trip acceptance.
if ! "$JLC_BIN" pcb export cpl --help >/dev/null 2>&1; then
  fail "installed JLC CLI lacks 'pcb export cpl'; install a harness build containing the SPINC Rev A CPL exporter before importing"
fi

stamp "Phase 1/5 — connect to the existing JLCEDA runtime (Web-first auto mode)"
run_logged "$JLC_BIN" eda bootstrap --host auto

stamp "Phase 2/5 — fail-closed KiCad import into a new JLCEDA project"
run_logged "$JLC_BIN" project import-external "$BUNDLE" \
  --file-type KiCad \
  --name "$PROJECT_NAME" \
  --switch=true

# External KiCad imports may surface as a standalone PCB rather than a classic
# board.pcb document. The mature activate command explicitly handles both.
run_logged "$JLC_BIN" activate pcb

stamp "Phase 3/5 — run JLCEDA PCB DRC before manufacturing export"
run_logged "$JLC_BIN" pcb drc

stamp "Phase 4/5 — export identity + placement + fabrication evidence"
BOM="$OUT_DIR/SPINC-JLC-Rev-A-BOM.csv"
CPL="$OUT_DIR/SPINC-JLC-Rev-A-CPL.csv"
GERBER="$OUT_DIR/SPINC-JLC-Rev-A-Gerber.zip"
EPRO2="$OUT_DIR/SPINC-JLC-Rev-A.epro2"
REPORT="$OUT_DIR/SPINC-JLC-Rev-A-roundtrip.json"

# --verify queries JLC's authoritative device identities for every Supplier
# Part in the actual migrated project's BOM and fails on Manufacturer Part drift.
run_logged "$JLC_BIN" bom export -o "$BOM" --format csv --verify
run_logged "$JLC_BIN" activate pcb
run_logged "$JLC_BIN" pcb export cpl -o "$CPL"
run_logged "$JLC_BIN" pcb export gerber -o "$GERBER"
run_logged "$JLC_BIN" project export -o "$EPRO2"

for evidence in "$BOM" "$CPL" "$GERBER" "$EPRO2"; do
  [[ -s "$evidence" ]] || fail "missing/empty runtime evidence: $evidence"
done

stamp "Phase 5/5 — round-trip migrated BOM/CPL against the frozen upstream board"
run_logged python3 JLC/verify_jlc_export.py \
  --bom "$BOM" \
  --cpl "$CPL" \
  --report "$REPORT"

# Produce a portable SHA-256 evidence manifest without depending on GNU coreutils
# (the normal operator machine may be macOS).
python3 - "$OUT_DIR" "$PROJECT_NAME" "$BUNDLE" <<'PY' | tee -a "$LOG"
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

out = Path(sys.argv[1]).resolve()
project = sys.argv[2]
bundle = Path(sys.argv[3]).resolve()
files = [
    bundle,
    out / "SPINC-JLC-Rev-A-BOM.csv",
    out / "SPINC-JLC-Rev-A-CPL.csv",
    out / "SPINC-JLC-Rev-A-Gerber.zip",
    out / "SPINC-JLC-Rev-A.epro2",
    out / "SPINC-JLC-Rev-A-roundtrip.json",
]

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

manifest = {
    "schema": 1,
    "name": "SPINC JLC Rev A runtime evidence",
    "project": project,
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "files": [
        {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
        for path in files
    ],
}
manifest_path = out / "SPINC-JLC-Rev-A-evidence.json"
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"[SPINC Rev A] evidence manifest: {manifest_path}")
PY

stamp "PASS — runtime import, DRC, native BOM/CPL/Gerber/epro2 export and BOM/CPL round-trip all passed"
stamp "NOT YET GOLDEN — Gerber outline/copper diff, JLCPCB DFM and physical-board bring-up remain mandatory"
