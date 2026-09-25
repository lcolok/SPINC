#!/usr/bin/env python3
"""Give KiCad NPTH pads whose pad equals the hole a near-zero copper size.

Why: KiCad emits no copper for an np_thru_hole pad whose size equals its drill.
JLCEDA Pro's KiCad importer turns the same pad into a hole-sized copper flash
on every copper layer. Measured 2026-09-25 on SPINC: 6 flashes per layer at
the SW1/SW3 (0.75 mm) and J1 USB-C (0.65 mm) locator pegs, 2.43 mm^2 of copper
per layer absent from the frozen KiCad Gerbers; real JLCPCB DFM reports such
holes as 0 mil annular ring (Ploopy J3, same importer). The harness's
verified pure-NPTH value is 0.001 mm.

JLCEDA derives the mask opening from the pad size, so shrinking the pad alone
closed the six mask openings (canary SPINC-NPTH-Canary). A pad-level
`(solder_mask_margin (hole - 0.001) / 2)` keeps the opening equal to the hole,
exactly as in the frozen Gerbers (canary SPINC-NPTH-Canary3: copper and mask
identical). Removing *.Cu from the pad instead made JLCEDA drop the NPTH
drill file entirely (canary SPINC-NPTH-Canary2) and must not be used.

Only `(pad "<name>" np_thru_hole circle|oval ... (size A B) ... (drill D ...))`
with size == drill is rewritten, and only its size token. An NPTH pad whose
copper is larger than its hole is an intentional ring and aborts the build.
"""
from __future__ import annotations

import re

TRANSFORM = "npth-zero-copper-v1"
NEAR_ZERO = "0.001 0.001"
_PAD = re.compile(r'\(pad "[^"]*" np_thru_hole (?:circle|oval)\b')
_SIZE = re.compile(r'\(size ([\d.]+) ([\d.]+)\)')
_DRILL = re.compile(r'\(drill (?:oval )?([\d.]+)(?: ([\d.]+))?')


def _pad_end(text: str, start: int) -> int:
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError("unterminated pad")


def zero_npth_copper(text: str) -> tuple[str, list[dict]]:
    out, pos, report = [], 0, []
    for m in _PAD.finditer(text):
        if m.start() < pos:
            continue
        end = _pad_end(text, m.start())
        pad = text[m.start():end]
        size, drill = _SIZE.search(pad), _DRILL.search(pad)
        if not size or not drill:
            raise ValueError(f"NPTH pad without size/drill: {pad[:60]}")
        sx, sy = float(size.group(1)), float(size.group(2))
        dx = float(drill.group(1))
        dy = float(drill.group(2)) if drill.group(2) else dx
        if (sx, sy) == (0.001, 0.001):
            continue  # already transformed (idempotent)
        if "(solder_mask_margin" in pad:
            raise ValueError("NPTH pad already declares a mask margin; refusing")
        if abs(sx - dx) > 1e-9 or abs(sy - dy) > 1e-9:
            raise ValueError(f"NPTH pad copper differs from hole ({sx}x{sy} vs {dx}x{dy}); refusing")
        at = re.search(r'\(at ([^)]*)\)', pad).group(1)
        margin = format((dx - 0.001) / 2, ".4f")
        if abs(dx - dy) > 1e-9:
            raise ValueError("oval NPTH mask margin is not supported; refusing")
        new_pad = (pad[:size.start()] + f"(size {NEAR_ZERO})\n\t\t\t(solder_mask_margin {margin})"
                   + pad[size.end():])
        out.append(text[pos:m.start()] + new_pad)
        pos = end
        report.append({"at": at, "hole": [dx, dy], "mask_margin": float(margin)})
    out.append(text[pos:])
    result = "".join(out)
    if _strip_npth_sizes(result) != _strip_npth_sizes(text):
        raise ValueError("content other than NPTH pad sizes changed")
    return result, report


def _strip_npth_sizes(text: str) -> str:
    out, pos = [], 0
    for m in _PAD.finditer(text):
        if m.start() < pos:
            continue
        end = _pad_end(text, m.start())
        pad = _SIZE.sub("(size _)", text[m.start():end], count=1)
        out.append(text[pos:m.start()] + re.sub(r"\n\t\t\t\(solder_mask_margin [\d.]+\)", "", pad, count=1))
        pos = end
    out.append(text[pos:])
    return "".join(out)
