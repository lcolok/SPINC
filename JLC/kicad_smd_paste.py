#!/usr/bin/env python3
"""Make JLCEDA honour KiCad SMD pads that deliberately carry no paste.

Why: KiCad expresses "no stencil aperture" by omitting *.Paste from a pad's
layers. JLCEDA Pro's KiCad importer ignores that and gives every copper SMD
pad a full-size paste opening. Measured 2026-09-25 on SPINC (canary
SPINC-NPTH-Canary3): full paste on the exposed pads of U3 (3.4x4.3), U5
(3.2x3.2) and U1 (0.9x1.6), whose paste the designer split into separate
stencil-window pads, and on TP1 and all four fiducials, which must stay bare.

The importer does honour a pad-level mask margin (canary 3), so for exactly
those pads this adds `(solder_paste_margin -m)` with m = half the pad's longer
side, shrinking any derived paste to nothing. KiCad ignores paste margin on a
pad without a paste layer, so the source semantics are unchanged. A pad that
already declares a paste margin aborts instead of being overwritten.
"""
from __future__ import annotations

import re

from kicad_npth_copper import _pad_end

TRANSFORM = "smd-no-paste-v1"
_SMD = re.compile(r'\(pad "[^"]*" smd \w+\b')
_SIZE = re.compile(r'\(size ([\d.]+) ([\d.]+)\)')
_LAYERS = re.compile(r'\(layers ([^)]*)\)')


def suppress_unpasted_smd(text: str) -> tuple[str, list[dict]]:
    out, pos, report = [], 0, []
    for m in _SMD.finditer(text):
        if m.start() < pos:
            continue
        end = _pad_end(text, m.start())
        pad = text[m.start():end]
        layers = re.findall(r'"([^"]+)"', _LAYERS.search(pad).group(1))
        has_cu = any(l.endswith(".Cu") for l in layers)
        has_paste = any(l.endswith(".Paste") for l in layers)
        if not has_cu or has_paste:
            continue
        margin = re.search(r'\(solder_paste_margin (-?[\d.]+)\)', pad)
        size = _SIZE.search(pad)
        sx, sy = float(size.group(1)), float(size.group(2))
        want = -round(max(sx, sy) / 2, 4)
        if margin:
            if float(margin.group(1)) == want:
                continue  # already transformed (idempotent)
            raise ValueError(f"SMD pad without paste layer already has paste margin {margin.group(1)}; refusing")
        new_pad = pad[:size.end()] + f"\n\t\t\t(solder_paste_margin {want:g})" + pad[size.end():]
        out.append(text[pos:m.start()] + new_pad)
        pos = end
        report.append({"at": re.search(r'\(at ([^)]*)\)', pad).group(1), "size": [sx, sy], "margin": want})
    out.append(text[pos:])
    result = "".join(out)
    if re.sub(r"\n\t\t\t\(solder_paste_margin -[\d.]+\)", "", result) != re.sub(r"\n\t\t\t\(solder_paste_margin -[\d.]+\)", "", text):
        raise ValueError("content other than added paste margins changed")
    return result, report
