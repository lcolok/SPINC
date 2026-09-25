# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["gerbonara==1.5.0", "shapely==2.1.1"]
# ///
"""Geometric equivalence gate: JLCEDA fabrication export vs frozen KiCad Gerbers.

Run with `uv run JLC/verify_gerber_equivalence.py --jlc <export.zip>`.

The frozen upstream production Gerbers (PCB/SPINC AA Charger/production/
SPINC_AA_Charger.zip) are the manufacturing truth. The JLCEDA export must
reproduce them. Gates (declared before measurement, not tuned to pass):

- drills: 1:1 hole and slot match (position/diameter 0.01 mm, plating equal);
  exact duplicate hits across JLC drill files are counted and reported;
- outline: closed board polygon symmetric difference <= 0.05 mm^2;
- copper (4 layers): every golden track/pad/flash must be covered by JLC
  copper and every JLC track/pad/flash by golden copper (uncovered area
  <= 0.02 mm^2 per layer); pour differences are reported, since JLCEDA
  re-pours the zones natively under the same 0.25 mm clearance;
- solder mask: every golden opening must also be open on JLC (uncovered
  <= 0.02 mm^2 per layer); extra JLC openings are reported;
- paste (stencil): both directions uncovered <= 0.02 mm^2 per layer; this
  was report-only until the importer fixes made it measurable, and was
  tightened, not loosened;
- silkscreen (both sides), gated since the KiCad import semantics repair
  (harness `jlc pcb epro2-repair-kicad-import`); the order was declared
  before measuring. The fab strips silk from solder-mask openings, so both
  sides are compared after removing golden mask openings. Every visible
  source text (frozen .kicad_pcb) defines a text zone from the glyph ink of
  both exports near its anchor:
    * non-text silk (outside all text zones; logos, outlines, pin-1 marks):
      both directions uncovered <= 0.02 mm^2 per layer;
    * each single-line text: ink capital height |JLC - golden| <= 0.05 mm;
      position <= 0.15 mm in the text's own frame: ink centre across the
      text and along a centred text, text box edge along a left/right
      justified one (KiCad's glyph side bearing is part of glyph shape);
    * each multi-line text block: height within 10 %, position <= 0.30 mm;
    * every ink component belongs to exactly one source text or is non-text;
    * text width is reported only: JLCEDA renders its default stroke font,
      semantically (capital height, anchor, justification) equivalent to
      KiCad's; glyph shapes and advance widths differ by that choice.

With --release-out and all gates passing, the JLC export is re-packed without
its JLC_DRAFT_NOT_RELEASED.txt marker (every other entry byte-identical) and
the release hash is recorded in the report. No release is written on failure.

Exit 0 = all gates pass; 5 = a gate failed; 1 = inputs unreadable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import shapely
from gerbonara import GerberFile
from gerbonara import graphic_primitives as gp
from gerbonara.graphic_objects import Region
from gerbonara.utils import MM
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parent))
import excellon_min  # noqa: E402
import kicad_silk_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ZIP = ROOT / "PCB/SPINC AA Charger/production/SPINC_AA_Charger.zip"
ARC_TOL = 0.002
POS_TOL = 0.01
COVER_TOL = 0.005        # geometric slack for float/arc flattening, mm
UNCOVERED_MAX = 0.02     # mm^2 per layer
OUTLINE_XOR_MAX = 0.05   # mm^2

G = "SPINC AA Charger-"
COPPER = [("F_Cu", "Gerber_TopLayer.GTL"), ("In1_Cu", "Gerber_InnerLayer1.G1"),
          ("In2_Cu", "Gerber_InnerLayer2.G2"), ("B_Cu", "Gerber_BottomLayer.GBL")]
MASK = [("F_Mask", "Gerber_TopSolderMaskLayer.GTS"), ("B_Mask", "Gerber_BottomSolderMaskLayer.GBS")]
PASTE = [("F_Paste", "Gerber_TopPasteMaskLayer.GTP"), ("B_Paste", "Gerber_BottomPasteMaskLayer.GBP")]
SILK = [("F_SilkS", "Gerber_TopSilkscreenLayer.GTO", "F_Mask", "F.SilkS"),
        ("B_SilkS", "Gerber_BottomSilkscreenLayer.GBO", "B_Mask", "B.SilkS")]
SOURCE_BOARD = ROOT / "PCB/SPINC AA Charger/SPINC AA Charger.kicad_pcb"
TEXT_H_TOL = 0.05        # mm, capital height, single-line text
TEXT_C_TOL = 0.15        # mm, ink centre, single-line text
BLOCK_H_REL = 0.10       # multi-line block height
BLOCK_C_TOL = 0.30       # mm, multi-line block centre
DRAFT_MARKER = "JLC_DRAFT_NOT_RELEASED.txt"
GOLDEN_DRILLS = [("PTH.drl", True), ("NPTH.drl", False)]
JLC_DRILLS = [("Drill_PTH_Through.DRL", True), ("Drill_PTH_Through_Via.DRL", True), ("Drill_NPTH_Through.DRL", False)]


def arc_points(x1, y1, x2, y2, cx, cy, clockwise):
    r = math.hypot(x1 - cx, y1 - cy)
    a1, a2 = math.atan2(y1 - cy, x1 - cx), math.atan2(y2 - cy, x2 - cx)
    full = math.isclose(x1, x2, abs_tol=1e-9) and math.isclose(y1, y2, abs_tol=1e-9)
    if full:
        a2 = a1 + (-2 * math.pi if clockwise else 2 * math.pi)
    elif clockwise:
        while a2 >= a1:
            a2 -= 2 * math.pi
    else:
        while a2 <= a1:
            a2 += 2 * math.pi
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - ARC_TOL / r))) if r > ARC_TOL else math.pi / 8
    n = max(2, int(abs(a2 - a1) / max(step, 1e-3)) + 1)
    return [(cx + r * math.cos(a1 + (a2 - a1) * i / n), cy + r * math.sin(a1 + (a2 - a1) * i / n)) for i in range(n + 1)]


def prim_geom(p):
    if isinstance(p, gp.Circle):
        return Point(p.x, p.y).buffer(p.r, quad_segs=32)
    if isinstance(p, gp.Line):
        if p.width <= 0:
            return None
        if math.isclose(p.x1, p.x2) and math.isclose(p.y1, p.y2):
            return Point(p.x1, p.y1).buffer(p.width / 2, quad_segs=32)
        return LineString([(p.x1, p.y1), (p.x2, p.y2)]).buffer(p.width / 2, quad_segs=32)
    if isinstance(p, gp.Arc):
        if p.width <= 0:
            return None
        return LineString(arc_points(p.x1, p.y1, p.x2, p.y2, p.cx, p.cy, p.clockwise)).buffer(p.width / 2, quad_segs=32)
    if isinstance(p, gp.Rectangle):
        g = box(p.x - p.w / 2, p.y - p.h / 2, p.x + p.w / 2, p.y + p.h / 2)
        return affinity.rotate(g, p.rotation, origin=(p.x, p.y), use_radians=True) if p.rotation else g
    if isinstance(p, gp.ArcPoly):
        outline, pts = list(p.outline), []
        for i, (x1, y1) in enumerate(outline):
            x2, y2 = outline[(i + 1) % len(outline)]
            arc = p.arc_centers[i] if i < len(p.arc_centers) else None
            if arc:
                clockwise, (cx, cy) = arc
                pts.extend(arc_points(x1, y1, x2, y2, cx, cy, clockwise)[:-1])
            else:
                pts.append((x1, y1))
        if len(pts) < 3:
            return None
        g = Polygon(pts)
        return g if g.is_valid else shapely.make_valid(g)
    raise TypeError(f"unsupported primitive {type(p).__name__}")


def layer_ops(path: Path):
    ops = []
    for obj in GerberFile.open(path).objects:
        is_region = isinstance(obj, Region)
        for p in obj.to_primitives(unit=MM):
            g = prim_geom(p)
            if g is not None and not g.is_empty:
                ops.append((bool(p.polarity_dark), g, is_region))
    return ops


def flatten(ops, only_regions=None):
    """Apply dark/clear in file order; optionally restrict to region or non-region objects."""
    acc, run, dark = shapely.Polygon(), [], None
    for d, g, reg in ops:
        if only_regions is not None and reg != only_regions:
            continue
        if dark is None or d == dark:
            run.append(g)
            dark = d
            continue
        u = shapely.union_all(run)
        acc = acc.union(u) if dark else acc.difference(u)
        run, dark = [g], d
    if run:
        u = shapely.union_all(run)
        acc = acc.union(u) if dark else acc.difference(u)
    return shapely.make_valid(acc)


def outline_polygon(path: Path):
    lines = []
    for obj in GerberFile.open(path).objects:
        for p in obj.to_primitives(unit=MM):
            if isinstance(p, gp.Line):
                lines.append(LineString([(p.x1, p.y1), (p.x2, p.y2)]))
            elif isinstance(p, gp.Arc):
                lines.append(LineString(arc_points(p.x1, p.y1, p.x2, p.y2, p.cx, p.cy, p.clockwise)))
    # KiCad writes endpoints with ~1e-5 mm rounding gaps; snap every vertex to
    # a 0.001 mm grid so shared corners coincide, then node and polygonize.
    merged = shapely.union_all([shapely.set_precision(line, 0.001) for line in lines])
    faces = sorted(shapely.polygonize(shapely.get_parts(merged)).geoms, key=lambda f: -f.area)
    if not faces:
        raise ValueError(f"{path.name}: outline does not close")
    board = faces[0]
    for hole in faces[1:]:
        if board.contains(hole.representative_point()):
            board = board.difference(hole)
    return board


def uncovered(a, b):
    return a.difference(b.buffer(COVER_TOL, quad_segs=8)).area


def match_holes(golden, jlc):
    """Greedy 1:1 nearest match within POS_TOL on position and diameter."""
    remaining = list(jlc)
    unmatched_golden = []
    for g in golden:
        best = None
        for i, j in enumerate(remaining):
            if j[3] == g[3] and abs(j[2] - g[2]) <= POS_TOL and math.hypot(j[0] - g[0], j[1] - g[1]) <= POS_TOL:
                best = i
                break
        if best is None:
            unmatched_golden.append(g)
        else:
            remaining.pop(best)
    return unmatched_golden, remaining


def slot_key(s):
    a, b = (s[0], s[1]), (s[2], s[3])
    return (min(a, b), max(a, b), s[4], s[5])


def match_slots(golden, jlc):
    remaining = [slot_key(s) for s in jlc]
    missing = []
    for g in map(slot_key, golden):
        hit = next((i for i, j in enumerate(remaining)
                    if j[3] == g[3] and abs(j[2] - g[2]) <= POS_TOL
                    and all(math.hypot(p[0] - q[0], p[1] - q[1]) <= POS_TOL for p, q in zip(g[:2], j[:2]))), None)
        if hit is None:
            missing.append(g)
        else:
            remaining.pop(hit)
    return missing, remaining


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_core(t, advance):
    """Glyph-assignment box of a source text in its own frame, relative to the anchor.

    advance is the per-character half-width factor of the font being probed
    (KiCad stroke font ~0.40 x size, JLC default font ~0.55 x size after the
    capital-height translation); vertical extent follows KiCad 8 line layout.
    """
    n = max(len(line) for line in t.lines)
    rows = len(t.lines)
    hw = n * t.size * advance
    interline = 1.68 * 0.9583 * t.size
    h = t.size * (1 + interline / t.size * (rows - 1))
    cx = {"left": hw, "center": 0.0, "right": -hw}[t.just_h]
    cy = {"bottom": h / 2, "middle": 0.0, "top": -h / 2}[t.just_v]
    hh = t.size * 0.6 if rows == 1 else h / 2 + t.size * 0.3
    return box(cx - hw, cy - hh, cx + hw, cy + hh)


def _to_text_frame(g, t):
    return affinity.rotate(affinity.translate(g, -t.x, -t.y), -t.angle, origin=(0, 0))


def _from_text_frame(g, t):
    return affinity.translate(affinity.rotate(g, t.angle, origin=(0, 0)), t.x, t.y)


def _arc3(p0, p1, p2):
    (x1, y1), (x2, y2), (x3, y3) = p0, p1, p2
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [p0, p2]
    ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / d
    uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / d
    cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
    return arc_points(x1, y1, x3, y3, ux, uy, cross < 0)


def silk_graphics_geometry(items):
    """Shapely ink of the source's non-text silk primitives (text segmentation mask)."""
    geoms = []
    for it in items:
        w, pts = it["width"], it["pts"]
        if it["type"] == "line":
            g = LineString(pts).buffer(w / 2, quad_segs=16) if w > 0 else None
        elif it["type"] == "arc":
            g = LineString(_arc3(*pts)).buffer(w / 2, quad_segs=16) if w > 0 else None
        elif it["type"] == "circle":
            (cx, cy), (ex, ey) = pts
            disk = Point(cx, cy).buffer(math.hypot(ex - cx, ey - cy), quad_segs=64)
            g = disk if it["fill"] else disk.exterior.buffer(w / 2, quad_segs=16)
        else:
            ring = LineString(list(pts) + [pts[0]])
            g = shapely.make_valid(Polygon(pts)) if it["fill"] else None
            if w > 0:
                g = ring.buffer(w / 2, quad_segs=16) if g is None else g.union(ring.buffer(w / 2, quad_segs=16))
        if g is not None and not g.is_empty:
            geoms.append(g)
    return shapely.union_all(geoms) if geoms else shapely.Polygon()


def _assign_glyphs(parts, texts, advance):
    """Assign every ink component to at most one text: the text whose glyph box
    contains the component centroid (nearest anchor on ties). Also returns the
    pairs of texts that share one component substantially (>= 20 % of its area
    inside each glyph box): overlapping source texts whose ink is merged."""
    tree = shapely.STRtree(parts)
    owner, shared = {}, {}
    for ti, t in enumerate(texts):
        core = _from_text_frame(_text_core(t, advance), t)
        for i in tree.query(core, predicate="intersects"):
            p = parts[i]
            if p.intersection(core).area >= 0.2 * p.area:
                shared.setdefault(i, set()).add(ti)
            c = p.centroid
            if not core.contains(c):
                continue
            d = math.hypot(c.x - t.x, c.y - t.y)
            if i not in owner or d < owner[i][1]:
                owner[i] = (ti, d)
    glyphs = [[] for _ in texts]
    for i, (ti, _) in owner.items():
        glyphs[ti].append(parts[i])
    unassigned = shapely.union_all([p for i, p in enumerate(parts) if i not in owner]) if parts else shapely.Polygon()
    pairs = {tuple(sorted(v)) for v in shared.values() if len(v) > 1}
    return glyphs, unassigned, pairs


def silk_compare(a, b, texts, graphics):
    """Semantic text + geometric non-text silkscreen comparison (see module doc).

    The source's own non-text silk (rendered from the .kicad_pcb) is removed
    before text glyphs are segmented, so glyphs touching outlines are not
    merged with them. Every remaining ink component must belong to exactly one
    source text on either side; anything else is unexplained ink.

    Position is compared in each text's frame: across the text (and along it
    when centred) by ink centre; along a left/right justified text by the text
    box edge, which KiCad puts thickness/1.52 inside the anchor (FONT::
    getLinePositions) and the JLC default font starts its ink at (no side
    bearing). The KiCad glyph side bearing (its ink inside that box edge) is
    only sanity-bounded, like advance width.
    """
    mask = silk_graphics_geometry(graphics).buffer(0.05, quad_segs=8)
    tg, tj = a.difference(mask), b.difference(mask)
    pa = [p for p in shapely.get_parts(tg) if not p.is_empty]
    pb = [p for p in shapely.get_parts(tj) if not p.is_empty]
    ga_all, stray_g, pairs = _assign_glyphs(pa, texts, 0.45)
    gb_all, stray_j, _ = _assign_glyphs(pb, texts, 0.60)
    # Texts that overlap in the source (merged golden ink) are compared as one
    # group, like a multi-line block; the source overlap is a design property.
    group = list(range(len(texts)))

    def root(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i
    for pair in pairs:
        for x in pair[1:]:
            group[root(x)] = root(pair[0])
    members = {}
    for i in range(len(texts)):
        members.setdefault(root(i), []).append(i)
    zones, rows, failures, groups = [], [], [], []
    for idx in [m for m in members.values() if len(m) > 1]:
        ga = shapely.union_all([g for i in idx for g in ga_all[i]])
        gb = shapely.union_all([g for i in idx for g in gb_all[i]])
        row = {"text": " + ".join(texts[i].text for i in idx), "kind": "overlapping-source-texts"}
        if ga.is_empty or gb.is_empty:
            row["error"] = "no ink"
            failures.append(row)
        else:
            (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = ga.bounds, gb.bounds
            row.update(golden_wh=[round(ax1 - ax0, 4), round(ay1 - ay0, 4)], jlc_wh=[round(bx1 - bx0, 4), round(by1 - by0, 4)],
                       centre_offset=round(math.hypot((ax0 + ax1 - bx0 - bx1) / 2, (ay0 + ay1 - by0 - by1) / 2), 4))
            ok = all(abs(j - g) <= BLOCK_H_REL * g for g, j in zip(row["golden_wh"], row["jlc_wh"])) and row["centre_offset"] <= BLOCK_C_TOL
            if not ok:
                failures.append(row)
            zones.append(box(min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)).buffer(0.1))
        groups.append(row)
    grouped = {i for m in members.values() if len(m) > 1 for i in m}
    for ti, (t, gl_a, gl_b) in enumerate(zip(texts, ga_all, gb_all)):
        if ti in grouped:
            continue
        row = {"text": t.text, "kind": t.kind}
        if not gl_a or not gl_b:
            row["error"] = "no golden ink" if not gl_a else "no JLC ink"
            failures.append(row)
            rows.append(row)
            continue
        ga, gb = shapely.union_all(gl_a), shapely.union_all(gl_b)
        fa, fb = _to_text_frame(ga, t), _to_text_frame(gb, t)
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = fa.bounds, fb.bounds
        multi = len(t.lines) > 1
        edge = t.thickness / 1.52
        if t.just_h == "left":
            along_j, bearing = bx0 - edge, ax0 - edge
        elif t.just_h == "right":
            along_j, bearing = -edge - bx1, -edge - ax1
        else:
            along_j, bearing = (bx0 + bx1 - ax0 - ax1) / 2, 0.0
        across = (by0 + by1 - ay0 - ay1) / 2
        row.update(golden_h=round(ay1 - ay0, 4), jlc_h=round(by1 - by0, 4),
                   golden_w=round(ax1 - ax0, 4), jlc_w=round(bx1 - bx0, 4),
                   along_offset=round(along_j, 4), across_offset=round(across, 4),
                   kicad_side_bearing=round(bearing, 4), glyphs=[len(gl_a), len(gl_b)])
        pos_tol = BLOCK_C_TOL if multi else TEXT_C_TOL
        h_ok = (abs(row["jlc_h"] - row["golden_h"]) <= BLOCK_H_REL * row["golden_h"]) if multi else \
               (abs(row["jlc_h"] - row["golden_h"]) <= TEXT_H_TOL)
        ok = h_ok and abs(along_j) <= pos_tol and abs(across) <= pos_tol and -0.01 <= bearing <= 0.35
        if not ok:
            failures.append(row)
        rows.append(row)
        zones.append(_from_text_frame(box(min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)), t).buffer(0.1))
    zone = shapely.union_all(zones) if zones else shapely.Polygon()
    na, nb = a.difference(zone), b.difference(zone)
    miss, extra = uncovered(na, nb), uncovered(nb, na)
    sg, sj = stray_g.area, stray_j.area
    ok = not failures and miss <= UNCOVERED_MAX and extra <= UNCOVERED_MAX and sg <= UNCOVERED_MAX and sj <= UNCOVERED_MAX
    return {"pass": ok, "texts": len(texts), "graphics": len(graphics),
            "text_failures": failures[:20], "text_failure_count": len(failures), "overlapping_text_groups": groups,
            "non_text_golden_uncovered_by_jlc_mm2": round(miss, 5), "non_text_jlc_uncovered_by_golden_mm2": round(extra, 5),
            "unexplained_golden_ink_mm2": round(sg, 5), "unexplained_jlc_ink_mm2": round(sj, 5),
            "non_text_golden_mm2": round(na.area, 3), "text_zone_mm2": round(zone.area, 3),
            "max_text_h_delta": round(max((abs(r["jlc_h"] - r["golden_h"]) for r in rows if "jlc_h" in r), default=0.0), 4),
            "max_text_position_offset": round(max((max(abs(r["along_offset"]), abs(r["across_offset"])) for r in rows if "along_offset" in r), default=0.0), 4),
            "text_rows": rows}


def run(golden_dir: Path, jlc_dir: Path, source_board: Path = SOURCE_BOARD) -> dict:
    report: dict = {"gates": {}, "measurements": {}}

    def gate(name, ok, **data):
        report["gates"][name] = {"pass": bool(ok), **data}

    gh, gs, jh, js = [], [], [], []
    for name, plated in GOLDEN_DRILLS:
        h, s = excellon_min.read(golden_dir / (G + name), plated)
        gh += h
        gs += s
    for name, plated in JLC_DRILLS:
        h, s = excellon_min.read(jlc_dir / name, plated)
        jh += h
        js += s
    exact = Counter((round(x, 4), round(y, 4), round(d, 4), p) for x, y, d, p in jh)
    duplicates = sum(n - 1 for n in exact.values() if n > 1)
    jh_unique = [(x, y, d, p) for (x, y, d, p) in exact]
    miss_h, extra_h = match_holes(gh, jh_unique)
    miss_s, extra_s = match_slots(gs, js)
    gate("drills", not (miss_h or extra_h or miss_s or extra_s),
         golden_holes=len(gh), jlc_holes_physical=len(jh_unique), jlc_duplicate_hits_across_files=duplicates,
         golden_slots=len(gs), jlc_slots=len(js),
         missing_holes=miss_h[:10], extra_holes=extra_h[:10], missing_slots=miss_s[:10], extra_slots=extra_s[:10])

    go, jo = outline_polygon(golden_dir / (G + "Edge_Cuts.gbr")), outline_polygon(jlc_dir / "Gerber_BoardOutlineLayer.GKO")
    xor = go.symmetric_difference(jo).area
    gate("outline", xor <= OUTLINE_XOR_MAX, golden_area_mm2=round(go.area, 4), jlc_area_mm2=round(jo.area, 4),
         xor_mm2=round(xor, 5), golden_bounds=[round(v, 4) for v in go.bounds], jlc_bounds=[round(v, 4) for v in jo.bounds])

    for gname, jname in COPPER:
        g_ops, j_ops = layer_ops(golden_dir / (G + gname + ".gbr")), layer_ops(jlc_dir / jname)
        g_all, j_all = flatten(g_ops), flatten(j_ops)
        g_feat, j_feat = flatten(g_ops, only_regions=False), flatten(j_ops, only_regions=False)
        g_miss, j_miss = uncovered(g_feat, j_all), uncovered(j_feat, g_all)
        x = g_all.symmetric_difference(j_all).area
        gate(f"copper:{gname}", g_miss <= UNCOVERED_MAX and j_miss <= UNCOVERED_MAX,
             golden_features_uncovered_by_jlc_mm2=round(g_miss, 5), jlc_features_uncovered_by_golden_mm2=round(j_miss, 5),
             golden_copper_mm2=round(g_all.area, 3), jlc_copper_mm2=round(j_all.area, 3),
             copper_xor_mm2=round(x, 3), copper_xor_rel=round(x / max(g_all.area, j_all.area, 1e-9), 5))

    for gname, jname in MASK:
        g_open, j_open = flatten(layer_ops(golden_dir / (G + gname + ".gbr"))), flatten(layer_ops(jlc_dir / jname))
        miss = uncovered(g_open, j_open)
        gate(f"mask:{gname}", miss <= UNCOVERED_MAX, golden_openings_not_open_on_jlc_mm2=round(miss, 5),
             jlc_extra_openings_mm2=round(uncovered(j_open, g_open), 5),
             golden_open_mm2=round(g_open.area, 3), jlc_open_mm2=round(j_open.area, 3))

    for gname, jname in PASTE:
        a, b = flatten(layer_ops(golden_dir / (G + gname + ".gbr"))), flatten(layer_ops(jlc_dir / jname))
        ga, jb = uncovered(a, b), uncovered(b, a)
        gate(f"paste:{gname}", ga <= UNCOVERED_MAX and jb <= UNCOVERED_MAX,
             golden_paste_missing_on_jlc_mm2=round(ga, 5), jlc_extra_paste_mm2=round(jb, 5),
             golden_mm2=round(a.area, 3), jlc_mm2=round(b.area, 3))

    texts = kicad_silk_text.silk_texts(source_board)
    for gname, jname, mname, klayer in SILK:
        mask = flatten(layer_ops(golden_dir / (G + mname + ".gbr")))
        a = flatten(layer_ops(golden_dir / (G + gname + ".gbr"))).difference(mask)
        b = flatten(layer_ops(jlc_dir / jname)).difference(mask)
        result = silk_compare(a, b, [t for t in texts if t.layer == klayer],
                              kicad_silk_text.silk_graphics(source_board, klayer))
        gate(f"silk:{gname}", result.pop("pass"), **result)
    report["pass"] = all(g["pass"] for g in report["gates"].values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jlc", type=Path, required=True, help="JLCEDA Gerber export zip")
    parser.add_argument("--golden", type=Path, default=GOLDEN_ZIP)
    parser.add_argument("--source-board", type=Path, default=SOURCE_BOARD, help="frozen .kicad_pcb (silk text placement)")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--release-out", type=Path, help="write the order-ready zip here only if every gate passes")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        gdir, jdir = Path(tmp, "golden"), Path(tmp, "jlc")
        try:
            for src, dst in ((args.golden, gdir), (args.jlc, jdir)):
                with zipfile.ZipFile(src) as z:
                    for info in z.infolist():
                        name = Path(info.filename).name
                        if name.lower().endswith((".gbr", ".drl", ".gko", ".gtl", ".gbl", ".g1", ".g2",
                                                  ".gts", ".gbs", ".gtp", ".gbp", ".gto", ".gbo")):
                            dst.mkdir(parents=True, exist_ok=True)
                            (dst / name).write_bytes(z.read(info))
            report = run(gdir, jdir, args.source_board)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            print(f"FAIL (inputs): {exc}")
            return 1
    report["inputs"] = {"golden": {"path": str(args.golden.relative_to(ROOT)) if args.golden.is_relative_to(ROOT) else str(args.golden),
                                   "sha256": sha256(args.golden)},
                        "jlc": {"path": str(args.jlc), "sha256": sha256(args.jlc)}}
    if args.release_out:
        args.release_out.unlink(missing_ok=True)
        if report["pass"]:
            args.release_out.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(args.jlc) as src, zipfile.ZipFile(args.release_out, "w", zipfile.ZIP_DEFLATED) as dst:
                kept = []
                for info in src.infolist():
                    if Path(info.filename).name == DRAFT_MARKER:
                        continue
                    dst.writestr(info, src.read(info))
                    kept.append(info.filename)
            report["release"] = {"path": str(args.release_out), "sha256": sha256(args.release_out),
                                 "entries": len(kept), "dropped": [DRAFT_MARKER]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True, default=list) + "\n", encoding="utf-8")
    for name, g in report["gates"].items():
        detail = {k: v for k, v in g.items() if k not in ("pass", "text_rows") and not (isinstance(v, list) and not v)}
        print(("PASS " if g["pass"] else "FAIL ") + name, json.dumps(detail, default=list))
    for name, m in report["measurements"].items():
        print("INFO " + name, json.dumps(m))
    if report.get("release"):
        print("RELEASE", json.dumps(report["release"]))
    print("RESULT:", "PASS" if report["pass"] else "FAIL")
    return 0 if report["pass"] else 5


if __name__ == "__main__":
    sys.exit(main())
