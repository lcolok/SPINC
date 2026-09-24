"""Offline tests for the exact DRC residual gate (no live JLCEDA calls)."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import verify_drc_residuals as gate

FIXTURE = Path(__file__).resolve().parent / "fixtures/drc-rev-a-411f5895.json"


def spec():
    return json.loads(gate.RESIDUALS.read_text(encoding="utf-8"))


def report():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def first_item(r, error_type):
    for group in r["raw_items"]:
        if group["name"] == error_type:
            return group["list"][0]["list"][0]
    raise KeyError(error_type)


class ResidualMatchTests(unittest.TestCase):
    def test_measured_report_exactly_matches_declared_residuals(self):
        extra, missing = gate.compare(gate.violation_keys(report()), gate.declared_keys(spec()))
        self.assertEqual((extra, missing), ([], []))

    def test_any_extra_violation_fails(self):
        r = report()
        item = copy.deepcopy(first_item(r, "Clearance Error"))
        item["obj1"]["suffix"] = "(GND): e2e2"
        r["raw_items"][0]["list"][0]["list"].append(item)
        r["total"] += 1
        extra, missing = gate.compare(gate.violation_keys(r), gate.declared_keys(spec()))
        self.assertEqual(len(extra), 1)
        self.assertEqual(missing, [])

    def test_changed_objects_fail_both_ways(self):
        r = report()
        first_item(r, "Clearance Error")["obj2"]["suffix"] = "(GND): H2_1"
        extra, missing = gate.compare(gate.violation_keys(r), gate.declared_keys(spec()))
        self.assertEqual((len(extra), len(missing)), (1, 1))

    def test_missing_declared_residual_fails(self):
        r = report()
        r["raw_items"] = [g for g in r["raw_items"] if g["name"] != "Netlist Error"]
        r["total"] = 1
        extra, missing = gate.compare(gate.violation_keys(r), gate.declared_keys(spec()))
        self.assertEqual(extra, [])
        self.assertEqual([m[0] for m in missing], ["Netlist Error"])

    def test_total_disagreeing_with_items_is_untrusted(self):
        r = report()
        r["total"] = 7
        with self.assertRaises(ValueError):
            gate.violation_keys(r)

    def test_clean_drc_parses_as_empty(self):
        self.assertEqual(sum(gate.violation_keys({"pass": True, "total": 0, "raw_items": []}).values()), 0)

    def test_residual_without_reason_is_rejected(self):
        s = spec()
        s["residuals"][0]["reason"] = "  "
        with self.assertRaises(ValueError):
            gate.declared_keys(s)


class NetlistEvidenceTests(unittest.TestCase):
    def test_component_difference_ignores_labels_frame_and_power(self):
        sch = [{"designator": "R1"}, {"designator": "C1"}, {"designator": "?", "value": "BAT_A"},
               {"designator": "", "value": "default420x297"}, {"designator": "#PWR01"}]
        pcb = [{"designator": "C1"}, {"designator": "REF**"}, {"designator": "REF**"}]
        self.assertEqual(gate.component_difference(sch, pcb), spec()["netlistComponentDifference"])

    def test_frozen_source_marks_r1_off_board(self):
        off = gate.off_board_symbols(gate.SCHEMATIC.read_text(encoding="utf-8"))
        self.assertIn("R1", off)
        self.assertTrue(set(spec()["netlistComponentDifference"]["schematicOnly"]) <= off)


if __name__ == "__main__":
    unittest.main()
