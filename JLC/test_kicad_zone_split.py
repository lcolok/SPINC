"""Offline tests for the multi-layer zone split applied to the migration bundle."""
from __future__ import annotations

import re
import unittest
from collections import Counter

import kicad_zone_split as zs
from verify_harness_pin import ROOT

FROZEN = ROOT / "PCB/SPINC AA Charger/SPINC AA Charger.kicad_pcb"


def fill(layer: str, x: int) -> str:
    return (f'\t\t(filled_polygon\n\t\t\t(layer "{layer}")\n\t\t\t(pts\n'
            f'\t\t\t\t(xy {x} 0) (xy {x + 1} 0) (xy {x + 1} 1)\n\t\t\t)\n\t\t)')


def zone(net: str, layers: str, uid: str, fills: list[str], extra: str = "") -> str:
    body = "\n".join([
        "\t(zone", "\t\t(net 1)", f'\t\t(net_name "{net}")', f"\t\t{layers}",
        f'\t\t(uuid "{uid}")', '\t\t(name "")', "\t\t(connect_pads", "\t\t\t(clearance 0.25)",
        "\t\t)", "\t\t(min_thickness 0.25)",
        "\t\t(polygon", "\t\t\t(pts", "\t\t\t\t(xy 0 0) (xy 9 0) (xy 9 9)", "\t\t\t)", "\t\t)",
        *([extra] if extra else []), *fills, "\t)",
    ])
    return body


def board(*zones: str) -> str:
    return "(kicad_pcb\n\t(version 20240108)\n" + "\n".join(zones) + '\n\t(gr_text "keep")\n)\n'


UID = "b081b0b6-14b6-4b0b-bea1-f324e7cf6629"


class SyntheticSplitTests(unittest.TestCase):
    def test_multilayer_zone_splits_into_single_layer_zones_with_own_fills(self):
        src = board(zone("GND", '(layers "F.Cu" "B.Cu")', UID, [fill("F.Cu", 1), fill("B.Cu", 2), fill("F.Cu", 3)]))
        out, report = zs.split_multilayer_zones(src)
        blocks = zs._zone_blocks(out)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(report, [{"uuid": UID, "layers": ["F.Cu", "B.Cu"],
                                   "filled_polygons": {"F.Cu": 2, "B.Cu": 1}}])
        for block, layer, count in zip(blocks, ("F.Cu", "B.Cu"), (2, 1)):
            self.assertIn(f'\t\t(layer "{layer}")', block)
            self.assertNotIn("(layers ", block)
            self.assertEqual(set(re.findall(r'\(filled_polygon\s+\(layer "([^"]+)"\)', block)), {layer})
            self.assertEqual(block.count("(filled_polygon"), count)
            self.assertIn("(clearance 0.25)", block)
        uuids = re.findall(r'\(uuid "([^"]+)"\)', out)
        self.assertEqual(len(set(uuids)), 2)
        self.assertNotIn(UID, uuids)
        self.assertTrue(out.endswith('\n\t(gr_text "keep")\n)\n'))

    def test_single_layer_zone_and_other_content_unchanged(self):
        src = board(zone("5V", '(layer "In2.Cu")', UID, [fill("In2.Cu", 1)]))
        out, report = zs.split_multilayer_zones(src)
        self.assertEqual(out, src)
        self.assertEqual(report, [])

    def test_idempotent_and_deterministic(self):
        src = board(zone("GND", '(layers "F.Cu" "In1.Cu" "B.Cu")', UID, [fill("F.Cu", 1), fill("B.Cu", 2)]))
        once, _ = zs.split_multilayer_zones(src)
        self.assertEqual(zs.split_multilayer_zones(once), (once, []))
        self.assertEqual(zs.split_multilayer_zones(src)[0], once)

    def test_fill_on_foreign_layer_fails_closed(self):
        src = board(zone("GND", '(layers "F.Cu" "B.Cu")', UID, [fill("In1.Cu", 1)]))
        with self.assertRaises(ValueError):
            zs.split_multilayer_zones(src)

    def test_unknown_per_layer_child_fails_closed(self):
        extra = '\t\t(fill_segments\n\t\t\t(layer "F.Cu")\n\t\t)'
        src = board(zone("GND", '(layers "F.Cu" "B.Cu")', UID, [fill("F.Cu", 1)], extra=extra))
        with self.assertRaisesRegex(ValueError, "unsupported per-layer"):
            zs.split_multilayer_zones(src)

    def test_unbalanced_zone_fails_closed(self):
        src = board(zone("GND", '(layers "F.Cu" "B.Cu")', UID, [fill("F.Cu", 1)])).replace("(min_thickness 0.25)", "(min_thickness 0.25")
        with self.assertRaises(ValueError):
            zs.split_multilayer_zones(src)


class FrozenBoardSplitTests(unittest.TestCase):
    def test_frozen_gnd_zone_splits_with_exact_per_layer_fill_counts(self):
        src = FROZEN.read_text(encoding="utf-8")
        out, report = zs.split_multilayer_zones(src)
        self.assertEqual(report, [{"uuid": UID, "layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                                   "filled_polygons": {"F.Cu": 8, "In1.Cu": 1, "In2.Cu": 70, "B.Cu": 5}}])
        nets = Counter()
        for block in zs._zone_blocks(out):
            net = re.search(r'\(net_name "([^"]*)"\)', block).group(1)
            layers = re.findall(r'^\t\t\(layers? ([^)]*)\)', block, re.M)
            self.assertEqual(len(layers), 1)
            self.assertNotIn(" ", layers[0])  # exactly one layer per zone
            nets[net] += 1
        self.assertEqual(nets, {"GND": 4, "5V": 1, "+3.3V": 1})
        self.assertEqual(zs._strip_zones(out), zs._strip_zones(src))


if __name__ == "__main__":
    unittest.main()
