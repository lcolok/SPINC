# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["gerbonara==1.5.0", "shapely==2.1.1", "numpy==2.2.6"]
# ///
"""Stackup and 3D-model gate: the migrated JLCEDA board has the source's
physical build, and its 3D model shows it (declared before measurement).

Run with `uv run JLC/verify_3d_stackup.py --epro2 <export.epro2> --obj <3d.zip>`.

- stackup: every source stackup layer with a thickness (frozen .kicad_pcb)
  equals the JLCEDA LAYER_PHYS thickness (mil) within max(0.0001 mm, 0.5 %),
  and their sum equals the source board thickness within 0.5 %;
- 3D mesh (JLCEDA get3DFile OBJ, the geometry the 3D preview renders):
  z extent within 5 % of the source board thickness; board material inside
  the battery-channel cutout <= 0.05 mm^2; board area (golden outline minus
  cutout, minus every golden drill grown by 0.15 mm) not covered by the mesh
  <= 0.5 mm^2.

Exit 0 = all gates pass; 5 = a gate failed; 1 = inputs unreadable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import excellon_min  # noqa: E402
import kicad_silk_text  # noqa: E402
import verify_gerber_equivalence as vge  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIL = 0.0254
LAYER_REL_TOL = 0.005
LAYER_ABS_TOL = 0.0001
TOTAL_REL_TOL = 0.005
Z_REL_TOL = 0.05
CUTOUT_MAX = 0.05
BOARD_MISSING_MAX = 0.5
DRILL_GROW = 0.15
FAB = {"F.Cu": 1, "B.Cu": 2, "F.SilkS": 3, "B.SilkS": 4, "F.Mask": 5, "B.Mask": 6, "F.Paste": 7, "B.Paste": 8}


def jlc_layer(name: str) -> int:
    if name in FAB:
        return FAB[name]
    if name.startswith("In") and name.endswith(".Cu"):
        return 14 + int(name[2:-3])
    if name.startswith("dielectric "):
        return 360 + int(name.split()[1])
    raise ValueError(f"no JLC layer for {name}")


def layer_phys(epro2: Path) -> dict[int, float]:
    with zipfile.ZipFile(epro2) as z:
        name = next(n for n in z.namelist() if n.endswith(".epru"))
        lines = z.read(name).decode("utf-8").split("\n")
    doc, out = None, {}
    for line in lines:
        head, sep, body = line.partition("||")
        if not sep:
            continue
        h = json.loads(head)
        if h["type"] == "DOCHEAD":
            doc = json.loads(body.rstrip("|")).get("docType")
        elif doc == "PCB" and h["type"] == "LAYER_PHYS":
            layer = json.loads(h["id"])[1]
            out[int(layer)] = float(json.loads(body.rstrip("|"))["thickness"])
    return out


def obj_mesh(obj_zip: Path):
    with zipfile.ZipFile(obj_zip) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".obj"))
        text = z.read(name).decode("utf-8")
    verts, faces = [], []
    for line in text.splitlines():
        if line.startswith("v "):
            verts.append([float(t) for t in line.split()[1:4]])
        elif line.startswith("f "):
            idx = [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
            faces.extend([idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1))
    return np.array(verts), np.array(faces)


def mesh_footprint(v, f):
    t = v[f][:, :, :2]
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    keep = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) > 2e-9
    rings = np.concatenate([t[keep], t[keep][:, :1]], axis=1)
    return shapely.union_all(shapely.polygons(rings)).buffer(0)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(epro2: Path, obj_zip: Path, board: Path, golden: Path) -> dict:
    report: dict = {"gates": {}}

    def gate(name, ok, **data):
        report["gates"][name] = {"pass": bool(ok), **data}

    general, layers = kicad_silk_text.stackup(board)
    phys = layer_phys(epro2)
    rows, bad, total = [], [], 0.0
    for name, mm in layers:
        jl = jlc_layer(name)
        got = phys.get(jl)
        got_mm = None if got is None else got * MIL
        ok = got_mm is not None and abs(got_mm - mm) <= max(LAYER_ABS_TOL, LAYER_REL_TOL * mm)
        total += got_mm or 0.0
        rows.append({"layer": name, "jlc_layer": jl, "source_mm": mm, "jlc_mil": got,
                     "jlc_mm": None if got_mm is None else round(got_mm, 5), "pass": ok})
        if not ok:
            bad.append(name)
    gate("stackup", not bad and abs(total - general) <= TOTAL_REL_TOL * general,
         source_board_thickness_mm=general, jlc_stack_total_mm=round(total, 5), failed_layers=bad, layers=rows)

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(golden) as z:
            for info in z.infolist():
                n = Path(info.filename).name
                if n.endswith((".gbr", ".drl")):
                    Path(tmp, n).write_bytes(z.read(info))
        outline = vge.outline_polygon(Path(tmp, vge.G + "Edge_Cuts.gbr"))
        drills = []
        for name, plated in vge.GOLDEN_DRILLS:
            holes, slots = excellon_min.read(Path(tmp, vge.G + name), plated)
            drills += [Point(x, y).buffer(d / 2 + DRILL_GROW) for x, y, d, _ in holes]
            drills += [shapely.LineString([(s[0], s[1]), (s[2], s[3])]).buffer(s[4] / 2 + DRILL_GROW) for s in slots]
    cutouts = [shapely.Polygon(r) for r in outline.interiors]
    cutout = shapely.union_all(cutouts) if cutouts else shapely.Polygon()
    v, f = obj_mesh(obj_zip)
    z_extent = float(v[:, 2].max() - v[:, 2].min())
    gate("mesh-thickness", abs(z_extent - general) <= Z_REL_TOL * general,
         mesh_z_extent_mm=round(z_extent, 5), source_board_thickness_mm=general)
    fp = mesh_footprint(v, f)
    inside = fp.intersection(cutout.buffer(-0.05)).area
    solid = outline.difference(shapely.union_all(drills)).buffer(-0.05)
    missing = solid.difference(fp).area
    gate("mesh-cutout", len(cutouts) >= 1 and inside <= CUTOUT_MAX,
         cutouts=len(cutouts), cutout_area_mm2=round(cutout.area, 3), material_inside_cutout_mm2=round(inside, 5))
    gate("mesh-board", missing <= BOARD_MISSING_MAX, board_solid_mm2=round(solid.area, 3),
         mesh_footprint_mm2=round(fp.area, 3), board_missing_in_mesh_mm2=round(missing, 5))
    report["pass"] = all(g["pass"] for g in report["gates"].values())
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epro2", type=Path, required=True)
    ap.add_argument("--obj", type=Path, required=True, help="jlc pcb export 3d --type obj output (zip)")
    ap.add_argument("--source-board", type=Path, default=vge.SOURCE_BOARD)
    ap.add_argument("--golden", type=Path, default=vge.GOLDEN_ZIP)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    try:
        report = run(args.epro2, args.obj, args.source_board, args.golden)
    except (OSError, ValueError, KeyError, StopIteration, zipfile.BadZipFile) as exc:
        print(f"FAIL (inputs): {exc}")
        return 1
    report["inputs"] = {k: {"path": str(p), "sha256": sha256(p)} for k, p in
                        (("epro2", args.epro2), ("obj", args.obj), ("source_board", args.source_board))}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, g in report["gates"].items():
        print(("PASS " if g["pass"] else "FAIL ") + name, json.dumps({k: v for k, v in g.items() if k not in ("pass", "layers")}))
    print("RESULT:", "PASS" if report["pass"] else "FAIL")
    return 0 if report["pass"] else 5


if __name__ == "__main__":
    sys.exit(main())
