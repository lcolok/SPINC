"""Offline tests for the NPTH copper and SMD paste importer transforms."""
from __future__ import annotations

import re
import unittest

import excellon_min
import kicad_npth_copper as npth
import kicad_smd_paste as paste
from verify_harness_pin import ROOT

FROZEN = ROOT / "PCB/SPINC AA Charger/SPINC AA Charger.kicad_pcb"
GOLDEN_DIR = ROOT / "PCB/SPINC AA Charger/production"


def npth_pad(size: str, drill: str, extra: str = "") -> str:
    return (f'\t\t(pad "" np_thru_hole circle\n\t\t\t(at 0 1.375 180)\n\t\t\t(size {size})\n{extra}'
            f'\t\t\t(drill {drill})\n\t\t\t(layers "*.Cu" "*.Mask")\n\t\t\t(uuid "u")\n\t\t)')


def smd_pad(layers: str, extra: str = "") -> str:
    return (f'\t\t(pad "9" smd rect\n\t\t\t(at 0 0)\n\t\t\t(size 3.4 4.3)\n{extra}'
            f'\t\t\t(layers {layers})\n\t\t\t(uuid "u")\n\t\t)')


class NpthCopperTests(unittest.TestCase):
    def test_hole_sized_npth_gets_near_zero_copper_and_hole_sized_mask(self):
        out, report = npth.zero_npth_copper(npth_pad("0.75 0.75", "0.75"))
        self.assertIn("(size 0.001 0.001)", out)
        self.assertIn("(solder_mask_margin 0.3745)", out)  # 0.001 + 2*0.3745 = 0.75
        self.assertIn("(drill 0.75)", out)
        self.assertIn('(layers "*.Cu" "*.Mask")', out)
        self.assertEqual(report, [{"at": "0 1.375 180", "hole": [0.75, 0.75], "mask_margin": 0.3745}])
        self.assertEqual(npth.zero_npth_copper(out), (out, []))

    def test_npth_with_copper_ring_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "differs from hole"):
            npth.zero_npth_copper(npth_pad("1.2 1.2", "0.75"))

    def test_existing_mask_margin_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "mask margin"):
            npth.zero_npth_copper(npth_pad("0.75 0.75", "0.75", "\t\t\t(solder_mask_margin 0.1)\n"))

    def test_frozen_board_has_exactly_the_six_locator_holes(self):
        out, report = npth.zero_npth_copper(FROZEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["hole"][0] for r in report), [0.65, 0.65, 0.75, 0.75, 0.75, 0.75])


class SmdPasteTests(unittest.TestCase):
    def test_copper_pad_without_paste_layer_gets_negative_margin(self):
        out, report = paste.suppress_unpasted_smd(smd_pad('"F.Cu" "F.Mask"'))
        self.assertIn("(solder_paste_margin -2.15)", out)
        self.assertEqual(report[0]["margin"], -2.15)
        self.assertEqual(paste.suppress_unpasted_smd(out), (out, []))

    def test_pasted_and_paste_only_pads_untouched(self):
        for layers in ('"F.Cu" "F.Paste" "F.Mask"', '"F.Paste"', '"B.Cu" "B.Paste" "B.Mask"'):
            with self.subTest(layers=layers):
                src = smd_pad(layers)
                self.assertEqual(paste.suppress_unpasted_smd(src), (src, []))

    def test_existing_paste_margin_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "paste margin"):
            paste.suppress_unpasted_smd(smd_pad('"F.Cu" "F.Mask"', "\t\t\t(solder_paste_margin -0.05)\n"))

    def test_frozen_board_suppresses_exactly_eight_pads(self):
        out, report = paste.suppress_unpasted_smd(FROZEN.read_text(encoding="utf-8"))
        sizes = sorted(tuple(r["size"]) for r in report)
        # U1/U3/U5 exposed pads, TP1, four fiducials.
        self.assertEqual(sizes, sorted([(0.9, 1.6), (3.4, 4.3), (3.2, 3.2), (1.5, 1.5)] + [(1.0, 1.0)] * 4))


class ExcellonReaderTests(unittest.TestCase):
    def test_golden_drills_parse_with_routed_slots(self):
        import zipfile, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(GOLDEN_DIR / "SPINC_AA_Charger.zip") as z:
            holes, slots = [], []
            for name, plated in (("SPINC AA Charger-PTH.drl", True), ("SPINC AA Charger-NPTH.drl", False)):
                path = Path(tmp, name)
                path.write_bytes(z.read(name))
                h, s = excellon_min.read(path, plated)
                holes += h
                slots += s
        self.assertEqual((len(holes), len(slots)), (165, 10))
        self.assertEqual(sum(1 for h in holes if abs(h[2] - 0.35) < 0.01), 136)

    def test_unknown_statement_fails_closed(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "x.drl")
            path.write_text("M48\nMETRIC\nT1C0.3\n%\nT1\nX1Y2\nG02X3Y4\nM30\n")
            with self.assertRaises(ValueError):
                excellon_min.read(path, True)


if __name__ == "__main__":
    unittest.main()
