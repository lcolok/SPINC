"""Minimal, auditable Excellon reader for KiCad and EasyEDA Pro drill files.

Returns holes (x, y, diameter, plated) and slots (x1, y1, x2, y2, diameter,
plated) in millimetres. Supports INCH/METRIC headers with decimal coordinates,
tool tables TnCd, drill hits, EasyEDA `G85` slots and KiCad routed slots
(G00 ... M15 G01 ... M16). Anything else aborts instead of being ignored.
"""
from __future__ import annotations

import re
from pathlib import Path

_COORD = re.compile(r"^X(-?[\d.]+)Y(-?[\d.]+)$")
_G85 = re.compile(r"^X(-?[\d.]+)Y(-?[\d.]+)G85X(-?[\d.]+)Y(-?[\d.]+)$")
_TOOLDEF = re.compile(r"^T(\d+)C([\d.]+)$")
_IGNORABLE = {"M48", "%", "G90", "G05", "M30", "FMAT,2", "M71", "M72", "G00", "G01"}


def read(path: Path, plated: bool) -> tuple[list, list]:
    scale, tools, tool = None, {}, None
    holes, slots = [], []
    route_start = None
    plunged = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("INCH"):
            scale = 25.4
            continue
        if line.startswith("METRIC"):
            scale = 1.0
            continue
        if line.startswith("; ") or line.startswith("M48"):
            continue
        m = _TOOLDEF.match(line)
        if m:
            if scale is None:
                raise ValueError(f"{path.name}: tool before units")
            tools[int(m.group(1))] = float(m.group(2)) * scale
            continue
        m = re.fullmatch(r"T(\d+)", line)
        if m:
            tool = int(m.group(1))
            if tool and tool not in tools:
                raise ValueError(f"{path.name}: undefined tool T{tool}")
            continue
        m = _G85.match(line)
        if m:
            x1, y1, x2, y2 = (float(v) * scale for v in m.groups())
            slots.append((x1, y1, x2, y2, tools[tool], plated))
            continue
        if line.startswith("G00X"):
            mm = _COORD.match(line[3:])
            route_start = tuple(float(v) * scale for v in mm.groups())
            continue
        if line == "M15":
            plunged = True
            continue
        if line.startswith("G01X"):
            if not plunged or route_start is None:
                raise ValueError(f"{path.name}: G01 without plunge")
            x2, y2 = (float(v) * scale for v in _COORD.match(line[3:]).groups())
            slots.append((*route_start, x2, y2, tools[tool], plated))
            continue
        if line == "M16":
            plunged, route_start = False, None
            continue
        m = _COORD.match(line)
        if m:
            if tool is None:
                raise ValueError(f"{path.name}: hit before tool select")
            holes.append((float(m.group(1)) * scale, float(m.group(2)) * scale, tools[tool], plated))
            continue
        if line in _IGNORABLE or line.startswith("G90") or line.startswith("FMAT"):
            continue
        raise ValueError(f"{path.name}: unsupported Excellon statement {line!r}")
    return holes, slots
