"""Offline tests for the order-package gate using the real run-2be4cd66 exports."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import verify_jlc_export as vx
import verify_order_package as op

FIX = Path(__file__).resolve().parent / "fixtures"


class OrderPackageTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(op.DELTAS.read_text(encoding="utf-8"))
        self.gb = vx.read_csv(op.ROOT / self.spec["orderFiles"]["bom"])
        self.gc = vx.read_csv(op.ROOT / self.spec["orderFiles"]["cpl"])
        self.jb = op.read_any_csv(FIX / "jlc-export-2be4cd66-bom.csv")
        self.jc = op.read_any_csv(FIX / "jlc-export-2be4cd66-cpl.csv")

    def problems(self, **override):
        args = dict(spec=self.spec, gold_bom_rows=self.gb, gold_cpl_rows=self.gc, jlc_bom_rows=self.jb, jlc_cpl_rows=self.jc)
        args.update(override)
        return op.check(**args)

    def row(self, rows, ref):
        return next(r for r in rows if r["Designator"].strip().strip('"') == ref)

    def test_measured_exports_match_declared_deltas_exactly(self):
        self.assertEqual(self.problems(), [])

    def test_jlc_cpl_is_utf16_tsv(self):
        self.assertTrue((FIX / "jlc-export-2be4cd66-cpl.csv").read_bytes().startswith(b"\xff\xfe"))
        self.assertEqual(len(self.jc), 101)  # 99 designators + 2 REF** logos
        self.assertEqual(sum(1 for r in self.jc if r["Designator"] == "REF**"), 2)

    def test_extra_placeholder_row_fails(self):
        jc = copy.deepcopy(self.jc) + [copy.deepcopy(self.row(self.jc, "REF**"))]
        self.assertTrue(any("REF**" in p for p in self.problems(jlc_cpl_rows=jc)))

    def test_undeclared_rotation_fails(self):
        jc = copy.deepcopy(self.jc)
        self.row(jc, "C1")["Rotation"] = "90"
        self.assertTrue(any("C1" in p for p in self.problems(jlc_cpl_rows=jc)))

    def test_changed_declared_position_delta_fails(self):
        jc = copy.deepcopy(self.jc)
        r = self.row(jc, "J4")
        r["Mid X"] = f"{float(r['Mid X'].replace('mm', '')) + 0.1}mm"
        self.assertTrue(any("J4" in p for p in self.problems(jlc_cpl_rows=jc)))

    def test_side_flip_fails(self):
        jc = copy.deepcopy(self.jc)
        self.row(jc, "C1")["Layer"] = "B"
        self.assertTrue(any("C1: side" in p for p in self.problems(jlc_cpl_rows=jc)))

    def test_missing_placement_fails(self):
        jc = [r for r in self.jc if r["Designator"] != "U5"]
        self.assertTrue(any("missing" in p and "U5" in p for p in self.problems(jlc_cpl_rows=jc)))

    def test_declared_delta_not_observed_fails(self):
        spec = copy.deepcopy(self.spec)
        spec["positionDeltasMm"]["C1"] = [0.5, 0.0]
        self.assertTrue(any("C1: declared position delta not observed" in p for p in self.problems(spec=spec)))

    def test_value_change_fails_and_alias_is_exact(self):
        jb = copy.deepcopy(self.jb)
        row = next(r for r in jb if "C11" in r["Designator"])
        row["Value"] = "22pF"
        self.assertTrue(any("C11" in p for p in self.problems(jlc_bom_rows=jb)))
        spec = copy.deepcopy(self.spec)
        spec["valueAliases"]["U8"]["jlc"] = "DS2712X"
        self.assertTrue(any("U8" in p for p in self.problems(spec=spec)))

    def test_order_bom_must_carry_c_numbers(self):
        gb = copy.deepcopy(self.gb)
        gb[0]["LCSC Part #"] = ""
        with self.assertRaises(ValueError):
            self.problems(gold_bom_rows=gb)


if __name__ == "__main__":
    unittest.main()
