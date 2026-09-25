# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["fonttools==4.55.3", "shapely==2.1.1"]
# ///
"""Build TrueType fonts that reproduce KiCad 8's built-in stroke font.

JLCEDA can only draw silk text in its own default font or in a font installed
on the machine that runs the web editor (SYS_FontManager.addFont takes a font
name, not a file). KiCad's stroke font "newstroke" is not published as a
font file, so this script converts KiCad's own glyph data into TTF outlines:

- source: common/newstroke_font.cpp of KiCad at KICAD_COMMIT, fetched and
  verified by SHA-256 (GPL-2.0-or-later; the generated fonts are derivative
  works under the same licence and are therefore not committed, only this
  deterministic builder is);
- glyph geometry follows STROKE_FONT::loadNewStrokeFont exactly: the first
  character pair holds the glyph's left/right bound, each further pair is a
  point (value - 'R'), " R" lifts the pen, y is offset by FONT_OFFSET (-8),
  one unit is 1/21 of the text size (capitals span 21 units);
- a KiCad stroke font has no weight: it is drawn with round-capped strokes of
  the text's thickness. A TTF has a fixed weight, so one font is built per
  thickness/size ratio (e.g. 0.150 for size 1.0 mm / thickness 0.15 mm),
  outlines = strokes buffered by thickness/2 with round caps and joins;
- advance width = glyph right bound - left bound (STROKE_FONT::GetTextAsGlyphs
  advances the cursor by the glyph box end; no extra letter spacing).

Font units: 100 per stroke unit, unitsPerEm 2100 (= one KiCad text size),
y up, baseline at 0 (KiCad's baseline row, capitals sit one unit below it as
in KiCad). Only U+0020..U+007E are built.

Usage: uv run JLC/fonts/build_kicad_newstroke_ttf.py --ratio 0.15 --ratio 0.2 --out ~/Library/Fonts
       ... --check   (rebuild in a temporary directory and require byte-identical
                      installed fonts in --out; exit 5 otherwise)
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import shapely
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from shapely.geometry import LineString, Point
from shapely.geometry.polygon import orient

KICAD_COMMIT = "d7388e2a70fc4d04f9acf05aa5c723ea310f9658"  # KiCad 8.0 branch, 2025-02-27
SOURCE_URL = f"https://gitlab.com/kicad/code/kicad/-/raw/{KICAD_COMMIT}/common/newstroke_font.cpp"
SOURCE_SHA256 = "a8afe7e54b4c0532d4aef878fa54e821fb5e84e67c42de9934837032baf066a2"
FONT_OFFSET = -8
UNIT = 100            # font units per stroke unit
UPEM = 21 * UNIT      # one KiCad text size
FIXED_TIME = 3786912000  # 2020-01-01 in the head table's 1904 epoch: deterministic bytes


def family_name(ratio: float) -> str:
    return f"KiCad Newstroke T{round(ratio * 1000):03d}"


def load_source(cache: Path | None) -> str:
    data = cache.read_bytes() if cache and cache.exists() else urllib.request.urlopen(SOURCE_URL, timeout=120).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256:
        raise SystemExit(f"newstroke_font.cpp sha256 {digest} != pinned {SOURCE_SHA256}")
    return data.decode("utf-8")


def glyph_strings(source: str) -> list[str]:
    body = source[source.index("newstroke_font[] ="):]
    body = body[body.index("{") + 1:body.index("};")]
    out = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', body):
        out.append(re.sub(r"\\(.)", r"\1", m.group(1)))
        if len(out) == 0x7F - 0x20:
            break
    return out


def parse_glyph(s: str):
    start, end = ord(s[0]) - ord("R"), ord(s[1]) - ord("R")
    strokes, cur = [], []
    for i in range(2, len(s), 2):
        a, b = s[i], s[i + 1]
        if a == " " and b == "R":
            if cur:
                strokes.append(cur)
            cur = []
            continue
        cur.append(((ord(a) - ord("R")) - start, (ord(b) - ord("R")) + FONT_OFFSET))
    if cur:
        strokes.append(cur)
    return end - start, strokes


def outline(strokes, radius_units: float):
    parts = []
    for st in strokes:
        pts = [(x * UNIT, -y * UNIT) for x, y in st]
        g = Point(pts[0]) if len(set(pts)) == 1 else LineString(pts)
        parts.append(g.buffer(radius_units * UNIT, quad_segs=16))
    return shapely.union_all(parts) if parts else shapely.Polygon()


def draw(pen, geom):
    for poly in shapely.get_parts(geom):
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        poly = orient(poly, sign=-1.0)  # TrueType: outer clockwise, holes counter-clockwise
        for ring in [poly.exterior, *poly.interiors]:
            pts = [(round(x), round(y)) for x, y in ring.coords[:-1]]
            dedup = [p for i, p in enumerate(pts) if p != pts[i - 1]]
            if len(dedup) < 3:
                continue
            pen.moveTo(dedup[0])
            for p in dedup[1:]:
                pen.lineTo(p)
            pen.closePath()


def build(ratio: float, glyphs: list[str], out: Path) -> Path:
    radius = ratio * 21 / 2  # stroke units
    names, cmap, advances, outlines = [".notdef"], {}, {".notdef": (21 * UNIT // 2, 0)}, {}
    empty = TTGlyphPen(None)
    outlines[".notdef"] = empty.glyph()
    for idx, s in enumerate(glyphs):
        cp = 0x20 + idx
        name = f"uni{cp:04X}"
        width, strokes = parse_glyph(s)
        pen = TTGlyphPen(None)
        geom = outline(strokes, radius)
        draw(pen, geom)
        outlines[name] = pen.glyph()
        lsb = round(geom.bounds[0]) if not geom.is_empty else 0
        advances[name] = (width * UNIT, lsb)
        names.append(name)
        cmap[cp] = name
    fam = family_name(ratio)
    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(outlines)
    fb.setupHorizontalMetrics(advances)
    ascent, descent = 24 * UNIT, 8 * UNIT
    fb.setupHorizontalHeader(ascent=ascent, descent=-descent)
    fb.setupNameTable({"familyName": fam, "styleName": "Regular", "uniqueFontIdentifier": f"{fam} {KICAD_COMMIT[:12]}",
                       "fullName": fam, "psName": fam.replace(" ", ""), "version": "Version 1.000",
                       "licenseDescription": "Derived from KiCad newstroke_font.cpp (GPL-2.0-or-later)"})
    fb.setupOS2(sTypoAscender=ascent, sTypoDescender=-descent, usWinAscent=ascent, usWinDescent=descent,
                sCapHeight=20 * UNIT, sxHeight=13 * UNIT, achVendID="SPNC")
    fb.setupPost()
    fb.font["head"].created = fb.font["head"].modified = FIXED_TIME
    path = out / (fam.replace(" ", "-") + ".ttf")
    fb.save(str(path))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ratio", type=float, action="append", required=True, help="stroke thickness / text size")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--source-cache", type=Path, help="local copy of newstroke_font.cpp (verified by hash)")
    ap.add_argument("--check", action="store_true", help="verify installed fonts in --out instead of writing")
    args = ap.parse_args()
    glyphs = glyph_strings(load_source(args.source_cache))
    if len(glyphs) != 0x7F - 0x20 or glyphs[0] != "JZ":
        print("unexpected glyph table", file=sys.stderr)
        return 1
    if args.check:
        bad = 0
        with tempfile.TemporaryDirectory() as tmp:
            for r in args.ratio:
                want = build(r, glyphs, Path(tmp))
                have = args.out.expanduser() / want.name
                ok = have.exists() and have.read_bytes() == want.read_bytes()
                bad += not ok
                print(("PASS " if ok else "FAIL ") + family_name(r), have, hashlib.sha256(want.read_bytes()).hexdigest())
        return 5 if bad else 0
    args.out.expanduser().mkdir(parents=True, exist_ok=True)
    for r in args.ratio:
        p = build(r, glyphs, args.out.expanduser())
        print(family_name(r), p, hashlib.sha256(p.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
