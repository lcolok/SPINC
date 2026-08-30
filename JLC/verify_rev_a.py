#!/usr/bin/env python3
"""Fail-fast guardrails for the SPINC JLC Rev A reproduction.

The purpose of Rev A is equivalence, not redesign. Run this script from any
working directory after touching the KiCad source, production BOM/positions,
or after exporting/mirroring data from JLCEDA Pro.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "JLC" / "rev-a-baseline.json"
PCB = ROOT / "PCB" / "SPINC AA Charger" / "SPINC AA Charger.kicad_pcb"
SCH = ROOT / "PCB" / "SPINC AA Charger" / "SPINC AA Charger.kicad_sch"
BOM = ROOT / "PCB" / "SPINC AA Charger" / "production" / "bom.csv"
POSITIONS = ROOT / "PCB" / "SPINC AA Charger" / "production" / "positions.csv"

errors: list[str] = []
passes: list[str] = []


def require(condition: bool, message: str) -> None:
    if condition:
        passes.append(message)
    else:
        errors.append(message)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def split_designators(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
pcb_text = PCB.read_text(encoding="utf-8")
sch_text = SCH.read_text(encoding="utf-8")
bom_rows = read_csv(BOM)
pos_rows = read_csv(POSITIONS)

# ---- Source PCB identity / manufacturing invariants -----------------------
require('(generator_version "8.0")' in pcb_text, "source PCB is KiCad 8")
require('(thickness 1.565)' in pcb_text, "source PCB thickness remains 1.565 mm")
for layer in ('(0 "F.Cu" signal)', '(1 "In1.Cu" signal)', '(2 "In2.Cu" signal)', '(31 "B.Cu" signal)'):
    require(layer in pcb_text, f"4-layer source contains {layer.split(chr(34))[1]}")
require('(copper_finish "ENIG")' in pcb_text, "source surface finish remains ENIG")
require('(color "Black")' in pcb_text, "source solder-mask profile contains black mask")

# Freeze a few unique Edge.Cuts primitives so accidental mechanical edits fail.
edge_fragments = (
    '(start 177 64.000008)\n\t\t(end 124.0001 64.000008)',
    '(start 118.000006 136.000008)\n\t\t(end 182 136.000008)',
    '(start 124 121)\n\t\t(end 124 81)',
    '(start 126 81)\n\t\t(end 174 81)',
    '(start 175 103)\n\t\t(end 160 103)',
)
for fragment in edge_fragments:
    require(fragment in pcb_text, "frozen Edge.Cuts primitive is unchanged")

# ---- BOM identity ---------------------------------------------------------
bom_by_ref: dict[str, dict[str, str]] = {}
for row in bom_rows:
    part = (row.get("LCSC Part #") or "").strip()
    require(bool(re.fullmatch(r"C\d+", part)), f"BOM row {row.get('Designator')} has a concrete LCSC part number")
    for ref in split_designators(row["Designator"]):
        require(ref not in bom_by_ref, f"BOM designator {ref} appears only once")
        bom_by_ref[ref] = row

expected_parts = {
    "U1": ("LM27761", "C129351"),
    "U3": ("W25Q64JVZPIQ", "C2940197"),
    "U4": ("AP2112K-3.3", "C51118"),
    "U5": ("RP2040", "C2040"),
    "U7": ("VCNL4040M3OE", "C142526"),
    "U8": ("DS2712E+", "C7455651"),
    "L1": ("SRP7050TA-470M", "C2047110"),
    "R34": ("0R124", "C875831"),
    "J4": ("Conn_01x01_Pin", "C3029553"),
    "J5": ("Conn_01x01_Pin", "C3029553"),
}
for ref, (value, lcsc) in expected_parts.items():
    row = bom_by_ref.get(ref)
    require(row is not None, f"critical BOM designator {ref} exists")
    if row:
        require(row["Value"].strip() == value, f"{ref} value is frozen to {value}")
        require(row["LCSC Part #"].strip() == lcsc, f"{ref} LCSC identity is frozen to {lcsc}")

# ---- Pick-and-place / mechanical placement freeze ------------------------
pos_by_ref = {row["Designator"].strip(): row for row in pos_rows}
tol = 0.001
for ref, expected in manifest["mechanical_freeze"]["critical_placements"].items():
    row = pos_by_ref.get(ref)
    require(row is not None, f"critical placement {ref} exists")
    if not row:
        continue
    require(abs(float(row["Mid X"]) - float(expected["x"])) <= tol, f"{ref} X placement is unchanged")
    require(abs(float(row["Mid Y"]) - float(expected["y"])) <= tol, f"{ref} Y placement is unchanged")
    require(abs(float(row["Rotation"]) - float(expected["rotation"])) <= tol, f"{ref} rotation is unchanged")
    require(row["Layer"].strip().lower() == expected["layer"], f"{ref} assembly side is unchanged")

non_fid_bottom = [
    row["Designator"]
    for row in pos_rows
    if row["Layer"].strip().lower() == "bottom" and not row["Designator"].startswith("FID")
]
require(not non_fid_bottom, f"no assembled component moved to bottom side ({', '.join(non_fid_bottom) or 'none'})")

# TH3 intentionally exists on the PCB/PnP reference set but is DNP in the schematic.
th3_at = sch_text.find('(property "Reference" "TH3"')
require(th3_at >= 0, "TH3 schematic reference exists")
if th3_at >= 0:
    th3_context = sch_text[max(0, th3_at - 500): th3_at + 200]
    require('(in_bom no)' in th3_context, "TH3 stays excluded from BOM")
    require('(dnp yes)' in th3_context, "TH3 stays explicitly DNP")
require("TH3" not in bom_by_ref, "TH3 is not accidentally added to the assembly BOM")

# ---- Rev A policy ---------------------------------------------------------
require(manifest["upstream"]["commit"] == "af7b36e8ca5e99bfb3e99d8b02d9864117091de7", "upstream golden commit is pinned")
require(manifest["manufacturing_profile"]["stackup"] == "JLC04161H-3313", "JLC Rev A stackup is pinned")
require(manifest["manufacturing_profile"]["layers"] == 4, "JLC Rev A remains four-layer")

print(f"SPINC JLC Rev A verification: {len(passes)} checks passed, {len(errors)} failed")
if errors:
    for item in errors:
        print(f"FAIL: {item}")
    sys.exit(1)

for item in passes:
    print(f"PASS: {item}")
