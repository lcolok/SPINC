#!/usr/bin/env python3
"""Generate the golden Rev A JLC/LCSC binding manifest from the source BOM.

The production BOM remains the source of truth for populated devices.  This
script groups every individual designator by its frozen LCSC C-number so the
JLCEDA system-library audit can stay read-only and deterministic.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "PCB" / "SPINC AA Charger" / "production" / "bom.csv"
OUTPUT = ROOT / "JLC" / "jlc-bindings.json"
DNP_REFERENCES = ["TH3"]


def ref_key(ref: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
    if match:
        return match.group(1), int(match.group(2))
    return ref, 0


def lcsc_key(value: str) -> int:
    match = re.fullmatch(r"C(\d+)", value)
    if not match:
        raise ValueError(f"invalid LCSC part number: {value!r}")
    return int(match.group(1))


def build_manifest() -> dict[str, object]:
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    grouped: dict[str, dict[str, object]] = {}
    component_count = 0

    for row in rows:
        refs = [ref.strip() for ref in row["Designator"].split(",") if ref.strip()]
        quantity = int(row["Quantity"])
        if len(refs) != quantity:
            raise ValueError(
                f"{row['Designator']}: quantity={quantity} but {len(refs)} designators were parsed"
            )

        lcsc = row["LCSC Part #"].strip()
        lcsc_key(lcsc)
        component_count += len(refs)

        item = grouped.setdefault(
            lcsc,
            {
                "lcsc": lcsc,
                "references": set(),
                "sourceValues": set(),
                "sourceFootprints": set(),
            },
        )
        item["references"].update(refs)
        item["sourceValues"].add(row["Value"].strip())
        item["sourceFootprints"].add(row["Footprint"].strip())

    bindings: list[dict[str, object]] = []
    for lcsc in sorted(grouped, key=lcsc_key):
        item = grouped[lcsc]
        bindings.append(
            {
                "lcsc": lcsc,
                "references": sorted(item["references"], key=ref_key),
                "sourceValues": sorted(item["sourceValues"]),
                "sourceFootprints": sorted(item["sourceFootprints"]),
            }
        )

    all_refs = [ref for item in bindings for ref in item["references"]]
    if len(all_refs) != len(set(all_refs)):
        raise ValueError("a designator appears in more than one LCSC binding")
    if set(all_refs) & set(DNP_REFERENCES):
        raise ValueError("DNP reference leaked into the populated-device binding manifest")

    return {
        "schema": 1,
        "intent": "Golden JLC Rev A populated-device binding manifest generated from the frozen KiCad production BOM.",
        "source": str(SOURCE.relative_to(ROOT)),
        "dnpReferences": DNP_REFERENCES,
        "componentCount": component_count,
        "uniqueLcscCount": len(bindings),
        "bindings": bindings,
    }


def render() -> str:
    return json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if JLC/jlc-bindings.json is not exactly regenerated from the source BOM",
    )
    args = parser.parse_args()

    expected = render()
    if args.check:
        if not OUTPUT.exists():
            print(f"FAIL: missing {OUTPUT.relative_to(ROOT)}")
            return 1
        actual = OUTPUT.read_text(encoding="utf-8")
        if actual != expected:
            print("FAIL: JLC/jlc-bindings.json is stale; run python JLC/build_jlc_binding_manifest.py")
            return 1
        manifest = json.loads(expected)
        print(
            "SPINC JLC binding manifest: PASS - "
            f"{manifest['componentCount']} populated designators / {manifest['uniqueLcscCount']} unique C-numbers"
        )
        return 0

    OUTPUT.write_text(expected, encoding="utf-8")
    manifest = json.loads(expected)
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: "
        f"{manifest['componentCount']} populated designators / {manifest['uniqueLcscCount']} unique C-numbers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
