#!/usr/bin/env python3
"""Fail-fast equivalence checks for the SPINC DS2712 buck power stage.

Golden JLC Rev A is a reproduction exercise.  This verifier deliberately
checks the upstream component identities, pin/net topology, power-stage
handoffs and placement cluster so a JLCEDA migration cannot silently turn a
successful import into an electrical redesign.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "JLC" / "critical-power-stage-audit.json"
PCB = ROOT / "PCB" / "SPINC AA Charger" / "SPINC AA Charger.kicad_pcb"
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
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def split_designators(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def footprint_block(pcb: str, ref: str) -> str:
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


def device_pads(ref: str, expected: list[dict[str, object]], label: str) -> dict[str, dict[str, str]]:
    block = footprint_block(pcb_text, ref)
    require(bool(block), f"{label}: {ref} footprint exists")
    if not block:
        return {}
    pads = pad_map(block)
    for item in expected:
        pin = str(item["pin"])
        actual = pads.get(pin)
        require(actual is not None, f"{label}: {ref}.{pin} exists")
        if not actual:
            continue
        require(actual["net"] == str(item["net"]), f"{label}: {ref}.{pin} net remains {item['net']}")
        if item.get("function") is not None:
            require(actual["function"] == str(item["function"]), f"{label}: {ref}.{pin} function remains {item['function']}")
    return pads


def two_terminal(spec: dict[str, object], label: str) -> dict[str, dict[str, str]]:
    ref = str(spec["reference"])
    block = footprint_block(pcb_text, ref)
    require(bool(block), f"{label}: {ref} footprint exists")
    if not block:
        return {}
    pads = pad_map(block)
    require(pads.get("1", {}).get("net") == str(spec["pad_1_net"]), f"{label}: {ref}.1 net remains {spec['pad_1_net']}")
    require(pads.get("2", {}).get("net") == str(spec["pad_2_net"]), f"{label}: {ref}.2 net remains {spec['pad_2_net']}")
    return pads


def bom_identity(ref: str, value: str, lcsc: str, label: str) -> None:
    row = bom_by_ref.get(ref)
    require(row is not None, f"{label}: {ref} exists in production BOM")
    if not row:
        return
    require(row["Value"].strip() == value, f"{label}: {ref} value/MPN remains {value}")
    require(row["LCSC Part #"].strip() == lcsc, f"{label}: {ref} LCSC identity remains {lcsc}")


def spec_bom_identity(spec: dict[str, object], label: str) -> None:
    value = str(spec.get("mpn", spec.get("value", "")))
    bom_identity(str(spec["reference"]), value, str(spec["lcsc"]), label)


power = json.loads(AUDIT.read_text(encoding="utf-8"))
pcb_text = PCB.read_text(encoding="utf-8")
bom_rows = read_csv(BOM)
pos_rows = read_csv(POSITIONS)

bom_by_ref: dict[str, dict[str, str]] = {}
for row in bom_rows:
    for ref in split_designators(row["Designator"]):
        bom_by_ref[ref] = row

require(power["schema"] == 1, "power-stage audit schema is supported")
require("DS2712" in power["intent"], "power-stage audit explicitly targets the DS2712 stage")

# ---- DS2712 control -> Q4 -> Q5 gate -------------------------------------
control = power["control_stage"]
q4_spec = control["gate_enable_mosfet"]
require(q4_spec["reference"] == "Q4", "gate-enable device remains Q4")
require(q4_spec["mpn"] == "DMG2301L", "Q4 remains DMG2301L")
require(q4_spec["lcsc"] == "C102619", "Q4 remains LCSC C102619")
q4 = device_pads("Q4", q4_spec["pads"], "charger control")
spec_bom_identity(q4_spec, "charger control")

r36_spec = control["gate_source_bias"]
r36 = two_terminal(r36_spec, "charger control gate bias")
spec_bom_identity(r36_spec, "charger control gate bias")

r37_spec = control["switch_gate_pullup"]
r37 = two_terminal(r37_spec, "switch gate pull-up")
spec_bom_identity(r37_spec, "switch gate pull-up")

u8_block = footprint_block(pcb_text, "U8")
require(bool(u8_block), "controller handoff: U8 footprint exists")
u8 = pad_map(u8_block) if u8_block else {}
for key, handoff in control["controller_handoff"].items():
    pin = str(handoff["pin"])
    require(u8.get(pin, {}).get("function") == handoff["function"], f"controller handoff: U8.{pin} remains {handoff['function']}")
    require(u8.get(pin, {}).get("net") == handoff["net"], f"controller handoff: U8.{pin}/{key} remains on {handoff['net']}")

require(u8.get("1", {}).get("net") == q4.get("1", {}).get("net"), "CC1 and Q4 gate remain one net")
require(u8.get("6", {}).get("net") == q4.get("3", {}).get("net"), "CSOUT and Q4 drain remain one net")
require(r36.get("1", {}).get("net") == q4.get("1", {}).get("net"), "R36.1 remains on Q4 gate/CC1")
require(r36.get("2", {}).get("net") == q4.get("2", {}).get("net"), "R36.2 remains on Q4 source/gate-control node")
require(r37.get("2", {}).get("net") == q4.get("2", {}).get("net"), "R37.2 remains on Q4 source/gate-control node")
require(r37.get("1", {}).get("net") == "5V", "R37.1 continues to pull the switch gate toward 5 V/off")

# ---- Main buck switching path --------------------------------------------
buck = power["buck_stage"]

c30_spec = buck["input_decoupling"]
c30 = two_terminal(c30_spec, "buck input decoupling")
spec_bom_identity(c30_spec, "buck input decoupling")

q5_spec = buck["switch_mosfet"]
require(q5_spec["reference"] == "Q5", "main charger switching device remains Q5")
require(q5_spec["mpn"] == "SI2305CDS", "Q5 remains SI2305CDS")
require(q5_spec["lcsc"] == "C37577", "Q5 remains LCSC C37577")
q5 = device_pads("Q5", q5_spec["pads"], "buck switch")
spec_bom_identity(q5_spec, "buck switch")

require(q5.get("1", {}).get("net") == q4.get("2", {}).get("net"), "Q5 gate remains driven from Q4 source/gate-control node")
require(q5.get("2", {}).get("net") == "5V", "Q5 source remains on 5 V")
require(c30.get("1", {}).get("net") == q5.get("2", {}).get("net"), "C30 remains local 5 V input decoupling for the buck source rail")
require(c30.get("2", {}).get("net") == "GND", "C30 return remains GND")

catch_spec = buck["catch_diode"]
d2 = two_terminal(catch_spec, "buck catch diode")
spec_bom_identity(catch_spec, "buck catch diode")

l1_spec = buck["inductor"]
l1 = two_terminal(l1_spec, "buck inductor")
spec_bom_identity(l1_spec, "buck inductor")
require(int(l1_spec["inductance_uH"]) == 47, "golden Rev A L1 remains 47 uH")

series_spec = buck["output_series_diode"]
d4 = two_terminal(series_spec, "buck output isolation diode")
spec_bom_identity(series_spec, "buck output isolation diode")

clamp_spec = buck["output_clamp"]
d3 = device_pads(str(clamp_spec["reference"]), clamp_spec["pads"], "buck output clamp")
spec_bom_identity(clamp_spec, "buck output clamp")
require(d3.get("2", {}).get("net", "").startswith("unconnected-(D3-"), "D3 package NC pin remains explicitly unconnected")

switch_node = q5.get("3", {}).get("net")
require(switch_node == d2.get("1", {}).get("net"), "Q5 drain and D2 cathode remain the same switch node")
require(switch_node == l1.get("2", {}).get("net"), "Q5/D2 switch node remains L1.2")
require(d2.get("2", {}).get("net") == "GND", "D2 anode remains on GND")

inductor_output = l1.get("1", {}).get("net")
require(inductor_output == d4.get("2", {}).get("net"), "L1 output remains D4 anode")
require(inductor_output == d3.get("1", {}).get("net"), "L1 output remains D3 clamp anode")
require(d3.get("3", {}).get("net") == "5V", "D3 clamp cathode remains on 5 V")

# ---- H-bridge / DS2712 sensing handoff -----------------------------------
handoff = power["bridge_handoff"]
q2_block = footprint_block(pcb_text, "Q2")
require(bool(q2_block), "bridge handoff: Q2 footprint exists")
q2 = pad_map(q2_block) if q2_block else {}

high_rail = str(handoff["high_rail_net"])
require(d4.get("1", {}).get("net") == high_rail, "D4 cathode remains the frozen battery high rail")
require(u8.get("15", {}).get("net") == high_rail, "U8.VP1 remains on the battery high rail")
require(q2.get("7", {}).get("net") == high_rail, "Q2 P-source high rail remains on the D4/VP1 rail")

r34_block = footprint_block(pcb_text, "R34")
r35_block = footprint_block(pcb_text, "R35")
r34 = pad_map(r34_block) if r34_block else {}
r35 = pad_map(r35_block) if r35_block else {}
require(bool(r34_block), "bridge handoff: R34 sense resistor exists")
require(bool(r35_block), "bridge handoff: R35 VN1 filter resistor exists")
low_rail = str(handoff["low_rail_net"])
require(q2.get("3", {}).get("net") == low_rail, "Q2 N-source low rail remains on the current-sense node")
require(r34.get("1", {}).get("net") == low_rail, "R34 high side remains on the H-bridge low rail")
require(r35.get("2", {}).get("net") == low_rail, "R35 feed remains on the H-bridge low rail")
require(r34.get("2", {}).get("net") == "GND", "R34 low side remains GND")
require(r35.get("1", {}).get("net") == u8.get("7", {}).get("net"), "R35 filtered side remains U8.VN1")

# ---- Power-stage placement cluster ---------------------------------------
pos_by_ref = {row["Designator"].strip(): row for row in pos_rows}
placement = power["placement_freeze"]
tol = float(placement["tolerance_mm"])
for ref, expected in placement["components"].items():
    row = pos_by_ref.get(ref)
    require(row is not None, f"power placement: {ref} exists")
    if not row:
        continue
    require(abs(float(row["Mid X"]) - float(expected["x"])) <= tol, f"power placement: {ref} X remains frozen")
    require(abs(float(row["Mid Y"]) - float(expected["y"])) <= tol, f"power placement: {ref} Y remains frozen")
    require(abs(float(row["Rotation"]) - float(expected["rotation"])) <= tol, f"power placement: {ref} rotation remains frozen")
    require(row["Layer"].strip().lower() == str(expected["layer"]).lower(), f"power placement: {ref} assembly side remains {expected['layer']}")

print(f"SPINC JLC Rev A power-stage verification: {len(passes)} checks passed, {len(errors)} failed")
if errors:
    for item in errors:
        print(f"FAIL: {item}")
    sys.exit(1)

for item in passes:
    print(f"PASS: {item}")
