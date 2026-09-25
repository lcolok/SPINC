#!/usr/bin/env python3
"""Split multi-layer KiCad zones into equivalent single-layer zones.

Why: JLCEDA Pro's KiCad importer turns one zone spanning N copper layers into
N per-layer pours but gives every one of them *all* the zone's cached
`filled_polygon`s. Measured 2026-09-24 on SPINC (GND on F/In1/In2/B with
8+1+70+5 = 84 fills): each JLC GND pour held the same 84 fills, overlapping
other nets on every layer (2973 zero-distance DRC hits), and a native re-pour
did not recompute them.

KiCad itself represents a multi-layer zone as the same outline and settings on
each layer with per-layer fills, so N single-layer zones carrying only their
own layer's fills are electrically and geometrically identical. The transform
is deterministic, touches nothing outside multi-layer zone blocks, and fails
closed on any per-layer content it does not understand.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter

TRANSFORM = "split-multilayer-zones-v1"
_ZONE_START = "\n\t(zone\n"
_ZONE_END = "\n\t)\n"
_LAYERS = re.compile(r'^\(layers((?:\s+"[^"]+")+)\)$')
_UUID = re.compile(r'^\(uuid "([0-9a-fA-F-]{36})"\)$')
_FILLED_LAYER = re.compile(r'^\(filled_polygon\s+\(layer "([^"]+)"\)')
# Namespace for per-layer zone UUIDs (uuid5 of a fixed URL; never changes).
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/lcolok/SPINC/JLC/kicad_zone_split/v1")


def _children(body: str) -> list[tuple[int, int]]:
    """Top-level s-expression spans inside a zone body (string-aware)."""
    spans, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(body):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced zone body")
            if depth == 0:
                spans.append((start, i + 1))
    if depth or in_str:
        raise ValueError("unterminated zone body")
    return spans


def _split_zone(block: str) -> tuple[list[str], dict | None]:
    """block is the text between '\\t(zone\\n' and '\\n\\t)' (exclusive)."""
    spans = _children(block)
    layers_idx = uuid_idx = None
    layers: list[str] = []
    for n, (a, b) in enumerate(spans):
        child = block[a:b]
        m = _LAYERS.match(child)
        if m:
            layers = re.findall(r'"([^"]+)"', m.group(1))
            layers_idx = n
        elif _UUID.match(child):
            uuid_idx = n
    if layers_idx is None or len(layers) < 2:
        return [block], None
    if uuid_idx is None:
        raise ValueError("multi-layer zone without uuid")
    if len(set(layers)) != len(layers):
        raise ValueError(f"duplicate layer in zone: {layers}")
    original_uuid = _UUID.match(block[slice(*spans[uuid_idx])]).group(1)
    fills: dict[str, list[str]] = {layer: [] for layer in layers}
    for n, (a, b) in enumerate(spans):
        child = block[a:b]
        if child.startswith("(filled_polygon"):
            m = _FILLED_LAYER.match(child)
            if not m or m.group(1) not in fills:
                raise ValueError("filled_polygon on a layer outside the zone")
            fills[m.group(1)].append(child)
        elif n != layers_idx and re.search(r'\(layers?\s+"', child):
            # Any other per-layer payload (e.g. legacy fill_segments) would be
            # silently duplicated or dropped; refuse instead.
            raise ValueError(f"unsupported per-layer zone child: {child[:40]}")
    out = []
    for layer in layers:
        pieces, cursor = [], 0
        for n, (a, b) in enumerate(spans):
            child = block[a:b]
            if n == layers_idx:
                replacement = f'(layer "{layer}")'
            elif n == uuid_idx:
                replacement = f'(uuid "{uuid.uuid5(_NS, original_uuid + "/" + layer)}")'
            elif child.startswith("(filled_polygon"):
                replacement = child if _FILLED_LAYER.match(child).group(1) == layer else None
            else:
                replacement = child
            if replacement is None:
                # Drop the child together with its leading whitespace.
                pieces.append(_strip_ws(block[cursor:a]))
            else:
                pieces.append(block[cursor:a] + replacement)
            cursor = b
        pieces.append(block[cursor:])
        out.append("".join(pieces))
    report = {"uuid": original_uuid, "layers": layers,
              "filled_polygons": {layer: len(fills[layer]) for layer in layers}}
    return out, report


def _strip_ws(gap: str) -> str:
    # The gap before a dropped child is pure indentation; keep nothing of it.
    if gap.strip():
        raise ValueError("unexpected content between zone children")
    return ""


def _fill_multiset(text: str) -> Counter:
    counter: Counter = Counter()
    for block in _zone_blocks(text):
        for a, b in _children(block):
            child = block[a:b]
            if child.startswith("(filled_polygon"):
                counter[child] += 1
    return counter


def _zone_line_spans(text: str) -> list[tuple[int, int]]:
    """(first, last) line indexes of every top-level zone, inclusive."""
    lines, spans, first = text.split("\n"), [], None
    for n, line in enumerate(lines):
        if first is None and line == "\t(zone":
            first = n
        elif first is not None and line == "\t)":
            spans.append((first, n))
            first = None
    if first is not None:
        raise ValueError("unterminated zone")
    return spans


def _zone_blocks(text: str) -> list[str]:
    lines = text.split("\n")
    return ["\n".join(lines[a + 1:b]) for a, b in _zone_line_spans(text)]


def split_multilayer_zones(text: str) -> tuple[str, list[dict]]:
    """Return (transformed text, per-zone report). Idempotent."""
    out, pos, reports = [], 0, []
    while True:
        i = text.find(_ZONE_START, pos)
        if i < 0:
            out.append(text[pos:])
            break
        body_start = i + len(_ZONE_START)
        j = text.find(_ZONE_END, body_start)
        if j < 0:
            raise ValueError("unterminated zone")
        out.append(text[pos:body_start])
        zones, report = _split_zone(text[body_start:j])
        out.append(zones[0])
        for extra in zones[1:]:
            out.append(_ZONE_END.rstrip("\n") + _ZONE_START + extra)
        if report:
            reports.append(report)
        pos = j
    result = "".join(out)
    # Conservation: identical multiset of filled polygons (their text carries
    # the layer), and nothing outside zone blocks changed.
    if _fill_multiset(result) != _fill_multiset(text):
        raise ValueError("filled polygon conservation failed")
    if _strip_zones(result) != _strip_zones(text):
        raise ValueError("content outside zones changed")
    return result, reports


def _strip_zones(text: str) -> str:
    lines, keep, cursor = text.split("\n"), [], 0
    for a, b in _zone_line_spans(text):
        keep.extend(lines[cursor:a])
        cursor = b + 1
    keep.extend(lines[cursor:])
    return "\n".join(keep)
