#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "Platformio" / "verify_firmware_contract.py"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the SPINC Golden Rev A firmware only through the governed AID remote PlatformIO path."
    )
    parser.add_argument(
        "--repro-audit",
        action="store_true",
        help="run a second verbose remote invocation with a distinct AID dedup key and verify the same Golden artifact digests; an identical historical done job may still be cache-hit",
    )
    args = parser.parse_args()

    run([sys.executable, str(VERIFY), "--runtime"])
    run(["pio", "run", "-d", "Platformio", "-e", "pico"])
    run([sys.executable, str(VERIFY), "--artifacts"])

    if args.repro_audit:
        # Verbose mode is recorded in the AID BuildJob params, so the second
        # invocation cannot reuse the first invocation's dedup key. Verbosity
        # does not alter compiled inputs, therefore artifact digests must match.
        # AID may still satisfy either key from an identical historical done job;
        # authoritative fresh-execution evidence must record cacheHit=false job IDs.
        run(["pio", "run", "-v", "-d", "Platformio", "-e", "pico"])
        run([sys.executable, str(VERIFY), "--artifacts"])

    contract = json.loads((ROOT / "Platformio" / "firmware-build-contract.json").read_text())
    print(json.dumps({
        "result": "passed",
        "transport": contract["officialBuild"]["transport"],
        "environment": contract["environment"]["name"],
        "uf2_sha256": contract["expectedArtifacts"]["pico-firmware.uf2"]["sha256"],
        "repro_audit": args.repro_audit,
        "qualification": "firmware-build-contract-only-not-device-flash-or-physical-golden",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
