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
CHARGER_AUDIT = ROOT / "JLC" / "critical-charger-audit.json"
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


def footprint_block(pcb: str, ref: str) -> str:
    """Return one top-level KiCad footprint block selected by Reference."""
    marker = f'(property "Reference" "{ref}"'
    ref_at = pcb.find(marker)
    if ref_at < 0:
        return ""
    start = pcb.rfind("\n\t(footprint ", 0, ref_at)
    if start < 0:
        return ""
    end = pcb.find("\n\t(footprint ", ref_at)
    if end < 0:
        end = len(pcb)
    return pcb[start:end]


def pad_map(block: str) -> dict[str, dict[str, str]]:
    """Extract pad -> {net,function} from one KiCad footprint block."""
    result: dict[str, dict[str, str]] = {}
    pad_starts = list(re.finditer(r'\n\t\t\(pad "([^"]*)"', block))
    for index, match in enumerate(pad_starts):
        pin = match.group(1)
        stop = pad_starts[index + 1].start() if index + 1 < len(pad_starts) else len(block)
        pad = block[match.start():stop]
        net_match = re.search(r'\(net \d+ "([^"]+)"\)', pad)
        fn_match = re.search(r'\(pinfunction "([^"]+)"\)', pad)
        result[pin] = {
            "net": net_match.group(1) if net_match else "",
            "function": fn_match.group(1) if fn_match else "",
        }
    return result


def verify_two_terminal_component(expected: dict[str, object], label: str) -> None:
    """Verify one audited two-terminal component's pad nets and BOM identity."""
    ref = str(expected["reference"])
    block = footprint_block(pcb_text, ref)
    require(bool(block), f"{label}: {ref} footprint exists")
    if not block:
        return

    pads = pad_map(block)
    require(pads.get("1", {}).get("net") == expected["pad_1_net"], f"{label}: {ref}.1 net is frozen")
    require(pads.get("2", {}).get("net") == expected["pad_2_net"], f"{label}: {ref}.2 net is frozen")

    row = bom_by_ref.get(ref)
    require(row is not None, f"{label}: {ref} exists in production BOM")
    if row:
        if "value" in expected:
            require(row["Value"].strip() == str(expected["value"]), f"{label}: {ref} value is frozen to {expected['value']}")
        if expected.get("lcsc"):
            require(row["LCSC Part #"].strip() == str(expected["lcsc"]), f"{label}: {ref} LCSC identity is frozen to {expected['lcsc']}")


manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
charger = json.loads(CHARGER_AUDIT.read_text(encoding="utf-8"))
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
    "R30": ("100", "C125923"),
    "R31": ("10k", "C98220"),
    "R32": ("22k", "C114065"),
    "R33": ("100k", "C14675"),
    "R34": ("0R124", "C875831"),
    "R35": ("10", "C109318"),
    "C15": ("1uF", "C15849"),
    "TH1": ("Thermistor_NTC", "C13564"),
    "J4": ("Conn_01x01_Pin", "C3029553"),
    "J5": ("Conn_01x01_Pin", "C3029553"),
}
for ref, (value, lcsc) in expected_parts.items():
    row = bom_by_ref.get(ref)
    require(row is not None, f"critical BOM designator {ref} exists")
    if row:
        require(row["Value"].strip() == value, f"{ref} value is frozen to {value}")
        require(row["LCSC Part #"].strip() == lcsc, f"{ref} LCSC identity is frozen to {lcsc}")

# ---- DS2712 pin/net equivalence ------------------------------------------
require(charger["device"]["reference"] == "U8", "charger audit targets U8")
require(charger["device"]["mpn"] == "DS2712E+", "charger audit MPN is DS2712E+")
require(charger["device"]["lcsc"] == "C7455651", "charger audit LCSC identity is C7455651")

u8_block = footprint_block(pcb_text, "U8")
require(bool(u8_block), "U8 footprint exists in source PCB")
u8_pads = pad_map(u8_block)
require(len(u8_pads) == 16, "U8 has exactly 16 physical pads")
for expected in charger["pins"]:
    pin = str(expected["pin"])
    actual = u8_pads.get(pin)
    require(actual is not None, f"U8 pin {pin}/{expected['name']} exists")
    if actual:
        require(actual["function"] == expected["name"], f"U8 pin {pin} function remains {expected['name']}")
        require(actual["net"] == expected["net"], f"U8 pin {pin}/{expected['name']} net remains {expected['net']}")

nc_pins = [str(pin) for pin in charger["critical_paths"]["single_cell_channel_policy"]["intentionally_unused_channel_2_pins"]]
for pin in nc_pins:
    actual = u8_pads.get(pin, {})
    require(actual.get("net", "").startswith("unconnected-(U8-"), f"U8 channel-2 pin {pin} stays explicitly NC")

# ---- DS2712 current-sense path -------------------------------------------
current_sense = charger["critical_paths"]["current_sense"]
for key in ("sense_resistor", "sense_filter_resistor"):
    expected = current_sense[key]
    ref = expected["reference"]
    block = footprint_block(pcb_text, ref)
    require(bool(block), f"{ref} current-sense footprint exists")
    pads = pad_map(block)
    require(pads.get("1", {}).get("net") == expected["pad_1_net"], f"{ref}.1 current-sense net is frozen")
    require(pads.get("2", {}).get("net") == expected["pad_2_net"], f"{ref}.2 current-sense net is frozen")

require(abs(float(current_sense["ds2712_typical_threshold_v"]) / float(current_sense["sense_resistor"]["value_ohm"]) - float(current_sense["nominal_regulation_current_a"])) < 1e-6, "documented nominal DS2712 regulation-current calculation is self-consistent")
require(u8_pads.get("7", {}).get("net") == current_sense["sense_filter_resistor"]["pad_1_net"], "U8.VN1 enters R35 sense-filter net")
require(u8_pads.get("8", {}).get("net") == "GND", "U8.VN0 remains at GND")

# ---- DS2712 analog programming / safety networks -------------------------
branch_pin_expectations = {
    "cell_test_threshold": ("10", "Net-(U8-CTST)"),
    "charge_timer": ("11", "Net-(U8-TMR)"),
    "controller_supply": ("12", "Net-(U8-VDD)"),
    "thermistor_channel_1": ("13", "Net-(U8-THM1)"),
}
for branch_name, (pin, net) in branch_pin_expectations.items():
    require(u8_pads.get(pin, {}).get("net") == net, f"{branch_name}: U8 pin {pin} remains on {net}")
    branch = charger["critical_paths"][branch_name]
    for component in branch.get("components", []):
        verify_two_terminal_component(component, branch_name)

thermistor = charger["critical_paths"]["thermistor_channel_1"]
th3_expected = thermistor["optional_dnp"]
th3_block = footprint_block(pcb_text, str(th3_expected["reference"]))
require(bool(th3_block), "thermistor_channel_1: TH3 optional DNP footprint exists")
if th3_block:
    th3_pads = pad_map(th3_block)
    require(th3_pads.get("1", {}).get("net") == th3_expected["pad_1_net"], "thermistor_channel_1: TH3.1 stays on THM1 net")
    require(th3_pads.get("2", {}).get("net") == th3_expected["pad_2_net"], "thermistor_channel_1: TH3.2 stays on GND")
require(bool(th3_expected["must_be_dnp"]), "thermistor_channel_1 audit requires TH3 DNP")
require(bool(th3_expected["must_be_excluded_from_bom"]), "thermistor_channel_1 audit requires TH3 BOM exclusion")

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
