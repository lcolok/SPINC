#!/usr/bin/env python3
"""Gate JLCEDA DRC on an exact, reviewed residual set.

Runs `jlc pcb drc --json` against the active SPINC-JLC-Rev-A PCB, stores the
full report, and passes only if the violation multiset equals
JLC/rev-a/drc-residuals.json. The netlist residual is additionally proven
live: the schematic/PCB component difference must be exactly the declared one,
and the frozen KiCad source must mark every schematic-only part off-board.

Exit 0 = exact match; 5 = any extra, missing or changed violation; 1 = could
not obtain trustworthy evidence. Nothing is waived by pattern or count.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESIDUALS = ROOT / "JLC/rev-a/drc-residuals.json"
SCHEMATIC = ROOT / "PCB/SPINC AA Charger/SPINC AA Charger.kicad_sch"
DEFAULT_REPORT = ROOT / "JLC/out/SPINC-JLC-Rev-A-DRC.json"
FIELDS = ("errorType", "errorObjType", "ruleName", "obj1", "obj2", "layer")


def violation_keys(report: dict) -> Counter:
    """Flatten a `jlc pcb drc --json` report into identity tuples."""
    keys: Counter = Counter()

    def walk(node):
        if isinstance(node, dict):
            if "errorObjType" in node:
                keys[(
                    node.get("errorType", ""), node.get("errorObjType", ""), node.get("ruleName", ""),
                    (node.get("obj1") or {}).get("suffix", ""), (node.get("obj2") or {}).get("suffix", ""),
                    node.get("layer", ""),
                )] += 1
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(report.get("raw_items", []))
    total = report.get("total")
    if report.get("pass") is True and not keys:
        return keys
    if not isinstance(total, int) or total != sum(keys.values()):
        raise ValueError(f"DRC total {total!r} disagrees with {sum(keys.values())} parsed items")
    return keys


def declared_keys(spec: dict) -> Counter:
    if spec.get("schema") != 1 or not isinstance(spec.get("residuals"), list):
        raise ValueError("unsupported residual spec")
    keys: Counter = Counter()
    for item in spec["residuals"]:
        if not isinstance(item, dict) or not str(item.get("reason", "")).strip():
            raise ValueError("every residual needs a reason")
        keys[tuple(str(item.get(f, "")) for f in FIELDS)] += 1
    return keys


def compare(actual: Counter, expected: Counter) -> tuple[list, list]:
    return sorted((actual - expected).elements()), sorted((expected - actual).elements())


def component_difference(sch: list[dict], pcb: list[dict]) -> dict:
    """Real components only: schematic net labels/ports carry designator '?'
    and the sheet frame has none; power symbols start with '#'."""
    def refs(rows):
        return {r.get("designator") for r in rows
                if r.get("designator") and r["designator"] != "?" and not r["designator"].startswith("#")}
    s, p = refs(sch), refs(pcb)
    return {"schematicOnly": sorted(s - p), "pcbOnly": sorted(p - s)}


def off_board_symbols(text: str) -> set[str]:
    out = set()
    for m in re.finditer(r"\n\t\(symbol\n", text):
        block = text[m.start():text.find("\n\t)\n", m.start() + 5)]
        ref = re.search(r'\(property "Reference" "([^"]+)"', block)
        if ref and "(on_board no)" in block:
            out.add(ref.group(1))
    return out


def jlc_json(*args: str, timeout: int = 600) -> tuple[int, object]:
    proc = subprocess.run(["jlc", *args, "--json"], capture_output=True, text=True, timeout=timeout)
    try:
        return proc.returncode, json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"jlc {' '.join(args)} returned no JSON (exit {proc.returncode})") from exc


def rows(value) -> list[dict]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, list):
                return item
    raise RuntimeError("unexpected component listing shape")


def live_component_difference() -> dict:
    subprocess.run(["jlc", "activate", "sch"], check=True, capture_output=True, timeout=120)
    try:
        _, sch = jlc_json("sch", "list-components", timeout=180)
    finally:
        subprocess.run(["jlc", "activate", "pcb"], check=True, capture_output=True, timeout=120)
    _, pcb = jlc_json("pcb", "list-components", timeout=180)
    return component_difference(rows(sch), rows(pcb))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    spec = json.loads(RESIDUALS.read_text(encoding="utf-8"))
    expected = declared_keys(spec)
    try:
        code, report = jlc_json("pcb", "drc", "--rpc-timeout", "8m", timeout=900)
        if code not in (0, 5) or not isinstance(report, dict):
            raise RuntimeError(f"jlc pcb drc exit {code}")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        actual = violation_keys(report)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        print(f"FAIL (evidence): {exc}")
        return 1
    extra, missing = compare(actual, expected)
    print(f"DRC items: {sum(actual.values())} (declared residuals: {sum(expected.values())})")
    for key in extra:
        print("UNEXPECTED:", dict(zip(FIELDS, key)))
    for key in missing:
        print("MISSING (declared but not reported):", dict(zip(FIELDS, key)))
    if extra or missing:
        return 5
    if any(k[0] == "Netlist Error" for k in actual.elements()):
        want = spec.get("netlistComponentDifference")
        try:
            got = live_component_difference()
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            print(f"FAIL (netlist evidence): {exc}")
            return 1
        print("live component difference:", json.dumps(got, sort_keys=True))
        if got != want:
            print("FAIL: live component difference differs from", json.dumps(want, sort_keys=True))
            return 5
        off = off_board_symbols(SCHEMATIC.read_text(encoding="utf-8"))
        if not set(got["schematicOnly"]) <= off:
            print("FAIL: schematic-only parts not marked (on_board no) in frozen source:", sorted(set(got["schematicOnly"]) - off))
            return 5
    print("PASS: DRC residuals exactly match JLC/rev-a/drc-residuals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
