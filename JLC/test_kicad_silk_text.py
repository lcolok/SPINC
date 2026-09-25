"""Source inventory behind the silkscreen and stackup gates (stdlib only)."""
from __future__ import annotations

import re
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_silk_text as kst  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "PCB/SPINC AA Charger/SPINC AA Charger.kicad_pcb"
FLOW = ROOT / "JLC/rev-a/flows/migrate.yaml"


class SourceSilkInventory(unittest.TestCase):
    def test_visible_texts(self):
        texts = kst.silk_texts(BOARD)
        self.assertEqual(Counter(t.layer for t in texts), Counter({"F.SilkS": 81}))
        self.assertEqual(sorted(t.text for t in texts if t.just_v == "bottom"), ["J3", "U7"])
        board = [t for t in texts if t.kind == "board"]
        self.assertEqual([t.text for t in board], ["AA Charger Rev. 2\nMaximilian Kern\n10/2024"])
        self.assertEqual({(t.size, t.thickness) for t in texts if t.kind == "designator"}, {(1.0, 0.15)})
        # hidden references are excluded from visible text but still parsed
        self.assertGreater(len(kst.silk_texts(BOARD, visible_only=False)), len(texts))

    def test_designator_anchor_transform(self):
        c15 = next(t for t in kst.silk_texts(BOARD) if t.text == "C15")
        # footprint C15 at (140.075, 70.925) rotated 0, reference offset (-0.025, -1.35); Gerber y is up
        self.assertAlmostEqual(c15.x, 140.05, places=6)
        self.assertAlmostEqual(c15.y, -69.575, places=6)

    def test_non_text_graphics(self):
        items = kst.silk_graphics(BOARD, "F.SilkS")
        self.assertEqual(Counter(i["type"] for i in items), Counter({"line": 234, "poly": 30, "arc": 6, "circle": 3}))
        self.assertEqual(kst.silk_graphics(BOARD, "B.SilkS"), [])

    def test_stackup_matches_board_thickness(self):
        general, layers = kst.stackup(BOARD)
        self.assertEqual(general, 1.565)
        self.assertAlmostEqual(sum(t for _, t in layers), general, places=9)
        self.assertEqual(dict(layers)["dielectric 2"], 1.24)

    def test_parser_decodes_escapes(self):
        node = kst.parse_sexpr('(gr_text "a\\nb \\"c\\"" (at 1 2))')
        self.assertEqual(node[1], 'a\nb "c"')
        with self.assertRaises(ValueError):
            kst.parse_sexpr("(a (b)")


class FlowGates(unittest.TestCase):
    def test_semantic_repair_and_gates_precede_order(self):
        ids = re.findall(r"^  - id: (\S+)$", FLOW.read_text(encoding="utf-8"), re.M)
        order = ["slot-regions", "export-imported-epro2", "repair-import-semantics", "import-semantic-project",
                 "switch-semantic-project", "pcb-drc", "export-gerber", "gerber-equivalence", "export-epro2",
                 "export-3d", "stackup-3d", "order-package"]
        self.assertEqual([i for i in ids if i in order], order)

    def test_order_package_requires_silk_and_stackup_evidence(self):
        text = FLOW.read_text(encoding="utf-8")
        order = text[text.index("  - id: order-package"):]
        self.assertIn("--stackup-3d-report JLC/out/SPINC-JLC-Rev-A4-stackup-3d.json", order)
        self.assertIn("--project SPINC-JLC-Rev-A4", order)
        eq = (ROOT / "JLC/verify_gerber_equivalence.py").read_text(encoding="utf-8")
        self.assertNotIn("REPORT_ONLY", eq)
        self.assertIn('gate(f"silk:{gname}"', eq)


if __name__ == "__main__":
    unittest.main()
