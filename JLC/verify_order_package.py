#!/usr/bin/env python3
"""Assemble the JLCPCB order package only if the JLCEDA migration is proven.

Order files: the Gerber released by verify_gerber_equivalence.py plus the
frozen upstream bom.csv and positions.csv (byte-identical). JLCEDA's own
BOM/CPL cannot be ordered as-is (no LCSC numbers survive a KiCad import; CPL
uses raw footprint origins), so they are used as cross-checks:

- JLC BOM: every order designator present with the same value; the only extra
  designators are the declared non-assembled/DNP ones;
- JLC CPL: same designators (plus declared board-only ones) on the same side;
  position and rotation differences exactly equal JLC/rev-a/order-deltas.json;
- Gerber: the equivalence report passed and names this exact release hash.

Exit 0 = package written to --out; 5 = a check failed (nothing written);
1 = unreadable inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import verify_jlc_export as vx

ROOT = Path(__file__).resolve().parents[1]
DELTAS = ROOT / "JLC/rev-a/order-deltas.json"
POS_TOL = 0.01
DELTA_TOL = 0.002
ROT_TOL = 0.01


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_any_csv(path: Path) -> list[dict[str, str]]:
    """JLCEDA writes CPL as UTF-16 TSV and BOM as UTF-8 CSV; accept either."""
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delimiter = "\t" if first.count("\t") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, strict=True)
    rows = list(reader)
    if not reader.fieldnames or any(None in r or None in r.values() for r in rows):
        raise ValueError(f"{path.name}: malformed table")
    return rows


def bom_values(rows: list[dict[str, str]], name: str) -> dict[str, str]:
    headers = rows[0].keys()
    ref_col = vx.first_column(headers, vx.BOM_DESIGNATOR_COLUMNS, "BOM designator")
    val_col = vx.first_column(headers, ("Value",), "BOM value")
    out: dict[str, str] = {}
    for row in rows:
        for ref in vx.refs_from_cell(row.get(ref_col, "")):
            if ref in out:
                raise ValueError(f"{name}: duplicate designator {ref}")
            out[ref] = (row.get(val_col) or "").strip()
    return out


def check(spec: dict, gold_bom_rows, gold_cpl_rows, jlc_bom_rows, jlc_cpl_rows) -> list[str]:
    problems: list[str] = []
    gold_lcsc = vx.flatten_bom(gold_bom_rows, "upstream bom.csv")  # requires a C-number per row
    gold_val, jlc_val = bom_values(gold_bom_rows, "upstream bom.csv"), bom_values(jlc_bom_rows, "JLC BOM")
    missing = sorted(set(gold_lcsc) - set(jlc_val))
    extra = sorted(set(jlc_val) - set(gold_lcsc))
    if missing:
        problems.append(f"order BOM designators missing from JLC BOM: {missing}")
    if extra != sorted(spec["jlcBomNotInOrderBom"]["designators"]):
        problems.append(f"JLC BOM extra designators {extra} != declared {sorted(spec['jlcBomNotInOrderBom']['designators'])}")
    aliases = spec.get("valueAliases", {})
    used_alias = set()
    for ref in sorted(set(gold_val) & set(jlc_val)):
        if gold_val[ref].lower() == jlc_val[ref].lower():
            continue
        alias = aliases.get(ref)
        if alias and alias["order"] == gold_val[ref] and alias["jlc"] == jlc_val[ref] and alias.get("reason"):
            used_alias.add(ref)
            continue
        problems.append(f"value differs for {ref}: order {gold_val[ref]!r} vs JLC {jlc_val[ref]!r}")
    for ref in sorted(set(aliases) - used_alias):
        problems.append(f"{ref}: declared value alias not observed")

    gold = vx.flatten_cpl(gold_cpl_rows, "upstream positions.csv")
    # KiCad's placeholder reference REF** (board-only graphics) is not a valid
    # designator for the strict parser; count it explicitly instead of hiding it.
    ref_col = vx.first_column(jlc_cpl_rows[0].keys(), vx.CPL_DESIGNATOR_COLUMNS, "CPL designator")
    placeholders = [r for r in jlc_cpl_rows if (r.get(ref_col) or "").strip() == "REF**"]
    jlc = vx.flatten_cpl([r for r in jlc_cpl_rows if r not in placeholders], "JLC CPL")
    if placeholders:
        jlc["REF**"] = {"placeholder": len(placeholders)}
    if len(placeholders) != spec["jlcOnlyPlacements"].get("placeholderRows", 0):
        problems.append(f"REF** placeholder rows {len(placeholders)} != declared {spec['jlcOnlyPlacements'].get('placeholderRows', 0)}")
    if sorted(set(gold) - set(jlc)):
        problems.append(f"order placements missing from JLC CPL: {sorted(set(gold) - set(jlc))}")
    if sorted(set(jlc) - set(gold)) != sorted(spec["jlcOnlyPlacements"]["designators"]):
        problems.append(f"JLC-only placements {sorted(set(jlc) - set(gold))} != declared")
    pos_decl, rot_decl = spec["positionDeltasMm"], spec["rotationPairs"]
    seen_pos, seen_rot = set(), set()
    for ref in sorted(set(gold) & set(jlc)):
        g, j = gold[ref], jlc[ref]
        if g["layer"] != j["layer"]:
            problems.append(f"{ref}: side {g['layer']} vs {j['layer']}")
        dx, dy = j["x"] - g["x"], j["y"] - g["y"]
        if math.hypot(dx, dy) > POS_TOL:
            want = pos_decl.get(ref)
            if want is None or abs(dx - want[0]) > DELTA_TOL or abs(dy - want[1]) > DELTA_TOL:
                problems.append(f"{ref}: undeclared or changed position delta ({dx:.4f},{dy:.4f}) vs {want}")
            seen_pos.add(ref)
        if vx.angle_error(g["rotation"], j["rotation"]) > ROT_TOL:
            want = rot_decl.get(ref)
            if (want is None or vx.angle_error(g["rotation"], want[0]) > ROT_TOL
                    or vx.angle_error(j["rotation"], want[1]) > ROT_TOL):
                problems.append(f"{ref}: undeclared or changed rotation {g['rotation']} -> {j['rotation']} vs {want}")
            seen_rot.add(ref)
    for ref in sorted(set(pos_decl) - seen_pos):
        problems.append(f"{ref}: declared position delta not observed")
    for ref in sorted(set(rot_decl) - seen_rot):
        problems.append(f"{ref}: declared rotation difference not observed")
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jlc-bom", type=Path, required=True)
    p.add_argument("--jlc-cpl", type=Path, required=True)
    p.add_argument("--gerber", type=Path, required=True)
    p.add_argument("--gerber-report", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    spec = json.loads(DELTAS.read_text(encoding="utf-8"))
    bom = ROOT / spec["orderFiles"]["bom"]
    cpl = ROOT / spec["orderFiles"]["cpl"]
    try:
        problems = check(spec, vx.read_csv(bom), vx.read_csv(cpl), read_any_csv(args.jlc_bom), read_any_csv(args.jlc_cpl))
        gerber_report = json.loads(args.gerber_report.read_text(encoding="utf-8"))
        gerber_sha = sha256(args.gerber)
    except (OSError, ValueError, KeyError) as exc:
        print(f"FAIL (inputs): {exc}")
        return 1
    if gerber_report.get("pass") is not True or (gerber_report.get("release") or {}).get("sha256") != gerber_sha:
        problems.append("Gerber is not the release named by a passing equivalence report")
    if args.out.exists():
        shutil.rmtree(args.out)
    if problems:
        for item in problems:
            print("FAIL:", item)
        return 5
    args.out.mkdir(parents=True)
    files = {"gerber": args.gerber, "bom": bom, "cpl": cpl}
    names = {"gerber": "SPINC-JLC-Rev-A-Gerber.zip", "bom": "SPINC-JLC-Rev-A-BOM.csv", "cpl": "SPINC-JLC-Rev-A-CPL.csv"}
    manifest = {"schema": 1, "board": "SPINC AA Charger Golden Rev A",
                "source_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
                "harness_commit": json.loads((ROOT / "JLC/harness-pin.json").read_text())["commit"],
                "gerber_equivalence": {k: v for k, v in gerber_report.items() if k in ("pass", "release")},
                "board_outline_mm": gerber_report["gates"]["outline"]["jlc_bounds"],
                "copper_layers": 4, "files": {}}
    for key, src in files.items():
        dst = args.out / names[key]
        shutil.copyfile(src, dst)
        manifest["files"][names[key]] = {"source": str(src.relative_to(ROOT)) if src.is_relative_to(ROOT) else str(src),
                                         "sha256": sha256(dst), "bytes": dst.stat().st_size}
    (args.out / "ORDER-MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("PASS: order package written to", args.out)
    for name, meta in manifest["files"].items():
        print(f"  {name}  {meta['bytes']} bytes  sha256 {meta['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
