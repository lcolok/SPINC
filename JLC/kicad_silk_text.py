"""Silkscreen text placement read from the frozen KiCad board (no rendering).

Used by the fabrication gates to locate every visible silk text in the
Gerber frame (mm, y up) so text ink can be compared semantically (capital
height and position) while all other silk is compared geometrically.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


def parse_sexpr(text: str):
    """Minimal KiCad S-expression parser: lists -> python lists, strings decoded."""
    i, n = 0, len(text)
    stack: list[list] = [[]]
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(":
            stack.append([])
            i += 1
        elif c == ")":
            node = stack.pop()
            stack[-1].append(node)
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while text[i] != '"':
                if text[i] == "\\":
                    i += 1
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(text[i], text[i]))
                else:
                    buf.append(text[i])
                i += 1
            i += 1
            stack[-1].append(Str("".join(buf)))
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()\"":
                j += 1
            stack[-1].append(text[i:j])
            i = j
    if len(stack) != 1 or len(stack[0]) != 1:
        raise ValueError("unbalanced S-expression")
    return stack[0][0]


class Str(str):
    """A quoted KiCad string (distinguished from bare atoms)."""


def lists(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def first(node, name):
    found = lists(node, name)
    return found[0] if found else None


def hidden(node) -> bool:
    h = first(node, "hide")
    if h is not None:
        return len(h) > 1 and h[1] == "yes"
    if "hide" in [c for c in node if isinstance(c, str) and not isinstance(c, Str)]:
        return True
    eff = first(node, "effects")
    if eff is not None:
        h = first(eff, "hide")
        if h is not None:
            return len(h) > 1 and h[1] == "yes"
        return "hide" in [c for c in eff if isinstance(c, str) and not isinstance(c, Str)]
    return False


@dataclass(frozen=True)
class SilkText:
    kind: str          # "designator" | "board"
    text: str
    layer: str
    x: float           # Gerber frame, mm, y up
    y: float
    angle: float       # degrees, CCW in the Gerber frame
    size: float        # KiCad capital height, mm
    thickness: float
    just_h: str        # left | center | right
    just_v: str        # top | middle | bottom

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")


def _text(node, text, layer, x, y, angle, kind) -> SilkText:
    eff = first(node, "effects")
    font = first(eff, "font")
    size = float(first(font, "size")[1])
    thick = float(first(font, "thickness")[1])
    jh, jv = "center", "middle"
    j = first(eff, "justify")
    if j is not None:
        for a in j[1:]:
            if a in ("left", "right"):
                jh = a
            elif a in ("top", "bottom"):
                jv = a
    return SilkText(kind, text, layer, x, -y, angle, size, thick, jh, jv)


def silk_texts(board: Path, visible_only: bool = True) -> list[SilkText]:
    root = parse_sexpr(board.read_text(encoding="utf-8"))
    out: list[SilkText] = []
    for fp in lists(root, "footprint"):
        at = first(fp, "at")
        fx, fy = float(at[1]), float(at[2])
        fr = float(at[3]) if len(at) > 3 else 0.0
        for pr in lists(fp, "property"):
            if pr[1] != "Reference":
                continue
            layer = first(pr, "layer")[1]
            if layer not in ("F.SilkS", "B.SilkS") or (visible_only and hidden(pr)):
                continue
            pa = first(pr, "at")
            lx, ly = float(pa[1]), float(pa[2])
            ang = float(pa[3]) if len(pa) > 3 else 0.0
            r = math.radians(fr)
            x = fx + lx * math.cos(r) + ly * math.sin(r)
            y = fy - lx * math.sin(r) + ly * math.cos(r)
            out.append(_text(pr, str(pr[2]), layer, x, y, ang, "designator"))
    for gt in lists(root, "gr_text"):
        layer = first(gt, "layer")[1]
        if layer not in ("F.SilkS", "B.SilkS") or (visible_only and hidden(gt)):
            continue
        at = first(gt, "at")
        ang = float(at[3]) if len(at) > 3 else 0.0
        out.append(_text(gt, str(gt[1]), layer, float(at[1]), float(at[2]), ang, "board"))
    return out


def stackup(board: Path) -> tuple[float, list[tuple[str, float]]]:
    """(general board thickness, [(layer name, thickness mm)]) from the source."""
    root = parse_sexpr(board.read_text(encoding="utf-8"))
    general = float(first(first(root, "general"), "thickness")[1])
    layers = []
    for layer in lists(first(first(root, "setup"), "stackup"), "layer"):
        t = first(layer, "thickness")
        if t is not None:
            layers.append((str(layer[1]), float(t[1])))
    return general, layers


SUPPORTED_SILK_GRAPHICS = ("fp_line", "fp_arc", "fp_circle", "fp_poly")


def silk_graphics(board: Path, layer: str) -> list[dict]:
    """Non-text silk primitives of one layer in the Gerber frame (mm, y up).

    Each item: {"type": line|arc|circle|poly, "pts": [(x, y), ...],
    "width": stroke mm, "fill": bool}. arc pts are (start, mid, end);
    circle pts are (center, point on circle). Unsupported silk graphics fail
    closed so a gate never silently ignores ink it cannot explain.
    """
    root = parse_sexpr(board.read_text(encoding="utf-8"))
    out: list[dict] = []

    def conv(fx, fy, fr, x, y):
        r = math.radians(fr)
        bx = fx + x * math.cos(r) + y * math.sin(r)
        by = fy - x * math.sin(r) + y * math.cos(r)
        return (bx, -by)

    def width(item):
        s = first(item, "stroke")
        w = first(s, "width") if s is not None else first(item, "width")
        return float(w[1]) if w is not None else 0.0

    def filled(item):
        f = first(item, "fill")
        return f is not None and len(f) > 1 and f[1] in ("solid", "yes")

    for fp in lists(root, "footprint"):
        at = first(fp, "at")
        fx, fy = float(at[1]), float(at[2])
        fr = float(at[3]) if len(at) > 3 else 0.0
        for item in fp:
            if not (isinstance(item, list) and item and isinstance(item[0], str) and item[0].startswith("fp_")):
                continue
            lay = first(item, "layer")
            if lay is None or lay[1] != layer or item[0] in ("fp_text",):
                continue
            if item[0] not in SUPPORTED_SILK_GRAPHICS:
                raise ValueError(f"unsupported silk graphic {item[0]} in {fp[1]}")
            p = lambda name: conv(fx, fy, fr, float(first(item, name)[1]), float(first(item, name)[2]))  # noqa: E731
            if item[0] == "fp_line":
                out.append({"type": "line", "pts": [p("start"), p("end")], "width": width(item), "fill": False})
            elif item[0] == "fp_arc":
                out.append({"type": "arc", "pts": [p("start"), p("mid"), p("end")], "width": width(item), "fill": False})
            elif item[0] == "fp_circle":
                out.append({"type": "circle", "pts": [p("center"), p("end")], "width": width(item), "fill": filled(item)})
            else:
                pts = first(item, "pts")
                if any(isinstance(q, list) and q[0] != "xy" for q in pts[1:]):
                    raise ValueError(f"fp_poly with non-xy points in {fp[1]}")
                out.append({"type": "poly", "pts": [conv(fx, fy, fr, float(q[1]), float(q[2])) for q in pts[1:]],
                            "width": width(item), "fill": filled(item)})
    for item in root:
        if isinstance(item, list) and item and isinstance(item[0], str) and item[0].startswith("gr_") and item[0] != "gr_text":
            lay = first(item, "layer")
            if lay is not None and lay[1] == layer:
                raise ValueError(f"board-level silk graphic {item[0]} is not supported")
    return out
