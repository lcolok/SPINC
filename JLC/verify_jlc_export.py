#!/usr/bin/env python3
"""Verify JLCEDA/JLCPCB BOM+CPL exports against the golden Rev A baseline.

This is intentionally a *round-trip* acceptance gate.  The KiCad production
BOM/CPL are the frozen upstream reference; after migrating into JLCEDA Pro,
export CSV BOM and pick-and-place data and feed them to this script.

A harmless global coordinate-origin translation is accepted.  Per-component
placement drift, assembly-side changes, rotation changes, missing/extra BOM
references, DNP leakage, and LCSC part-number changes fail the gate.

The verifier can also evaluate a Y-axis coordinate-convention inversion because
CAD exporters may use opposite Y handedness.  That mode transforms rotations
accordingly and is reported explicitly; it does not silently waive individual
rotation errors.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
BASE_BOM = ROOT / "PCB" / "SPINC AA Charger" / "production" / "bom.csv"
BASE_CPL = ROOT / "PCB" / "SPINC AA Charger" / "production" / "positions.csv"
DEFAULT_REPORT = ROOT / "JLC" / "out" / "jlc-export-verification.json"

DNP_REFS = {"TH3"}
CPL_ONLY_ALLOWED = {"TH3", "FID1", "FID2", "FID3", "FID4"}

BOM_DESIGNATOR_COLUMNS = ("Designator", "Reference", "References", "Ref", "Ref Des")
BOM_LCSC_COLUMNS = (
    "LCSC Part #",
    "LCSC Part Number",
    "JLCPCB Part #",
    "JLCPCB Part Number",
    "JLC Part #",
)
CPL_DESIGNATOR_COLUMNS = BOM_DESIGNATOR_COLUMNS
CPL_X_COLUMNS = ("Mid X", "Mid X (mm)", "MidX", "X", "X (mm)")
CPL_Y_COLUMNS = ("Mid Y", "Mid Y (mm)", "MidY", "Y", "Y (mm)")
CPL_ROT_COLUMNS = ("Rotation", "Rot", "Rotation (deg)")
CPL_LAYER_COLUMNS = ("Layer", "Side")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def first_column(headers: Iterable[str], aliases: tuple[str, ...], label: str) -> str:
    normalized = {h.strip().lower(): h for h in headers if h is not None}
    for alias in aliases:
        match = normalized.get(alias.lower())
        if match is not None:
            return match
    raise ValueError(f"missing {label} column; accepted names: {', '.join(aliases)}")


def refs_from_cell(value: str) -> list[str]:
    # Designators in JLC/KiCad BOMs are commonly comma-separated; regex keeps
    # the parser tolerant of semicolon/space-separated exports without relying
    # on a particular grouping style.
    return re.findall(r"[A-Za-z]+\d+", value or "")


def normalize_lcsc(value: str) -> str:
    match = re.search(r"\bC\d+\b", (value or "").upper())
    return match.group(0) if match else (value or "").strip()


def parse_mm(value: str) -> float:
    text = (value or "").strip().lower().replace("mm", "").replace("mil", "")
    return float(text)


def normalize_layer(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"t", "top", "front", "f", "topsurface", "toplayer"}:
        return "top"
    if text in {"b", "bottom", "back", "rear", "bottomsurface", "bottomlayer"}:
        return "bottom"
    return text


def angle_error(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def flatten_bom(rows: list[dict[str, str]], source_name: str) -> dict[str, dict[str, str]]:
    if not rows:
        raise ValueError(f"{source_name}: BOM is empty")
    headers = rows[0].keys()
    designator_col = first_column(headers, BOM_DESIGNATOR_COLUMNS, "BOM designator")
    lcsc_col = first_column(headers, BOM_LCSC_COLUMNS, "BOM LCSC/JLC part number")

    result: dict[str, dict[str, str]] = {}
    for row in rows:
        refs = refs_from_cell(row.get(designator_col, ""))
        if not refs:
            continue
        lcsc = normalize_lcsc(row.get(lcsc_col, ""))
        for ref in refs:
            if ref in result:
                raise ValueError(f"{source_name}: duplicate BOM designator {ref}")
            result[ref] = {"lcsc": lcsc}
    return result


def flatten_cpl(rows: list[dict[str, str]], source_name: str) -> dict[str, dict[str, object]]:
    if not rows:
        raise ValueError(f"{source_name}: CPL is empty")
    headers = rows[0].keys()
    designator_col = first_column(headers, CPL_DESIGNATOR_COLUMNS, "CPL designator")
    x_col = first_column(headers, CPL_X_COLUMNS, "CPL Mid X")
    y_col = first_column(headers, CPL_Y_COLUMNS, "CPL Mid Y")
    rot_col = first_column(headers, CPL_ROT_COLUMNS, "CPL rotation")
    layer_col = first_column(headers, CPL_LAYER_COLUMNS, "CPL layer")

    result: dict[str, dict[str, object]] = {}
    for row in rows:
        refs = refs_from_cell(row.get(designator_col, ""))
        if not refs:
            continue
        if len(refs) != 1:
            raise ValueError(f"{source_name}: CPL row must contain exactly one designator, got {refs}")
        ref = refs[0]
        if ref in result:
            raise ValueError(f"{source_name}: duplicate CPL designator {ref}")
        result[ref] = {
            "x": parse_mm(row.get(x_col, "")),
            "y": parse_mm(row.get(y_col, "")),
            "rotation": float((row.get(rot_col, "") or "0").strip()),
            "layer": normalize_layer(row.get(layer_col, "")),
        }
    return result


def transformed_xy(item: dict[str, object], mode: str) -> tuple[float, float]:
    x = float(item["x"])
    y = float(item["y"])
    if mode == "identity":
        return x, y
    if mode == "mirror-y-coordinate-frame":
        return x, -y
    raise ValueError(mode)


def transformed_rotation(rotation: float, mode: str) -> float:
    if mode == "identity":
        return rotation % 360.0
    if mode == "mirror-y-coordinate-frame":
        return (-rotation) % 360.0
    raise ValueError(mode)


def fit_mode(
    baseline: dict[str, dict[str, object]],
    actual: dict[str, dict[str, object]],
    refs: list[str],
    mode: str,
) -> dict[str, float | str]:
    dxs: list[float] = []
    dys: list[float] = []
    for ref in refs:
        bx, by = transformed_xy(baseline[ref], mode)
        dxs.append(float(actual[ref]["x"]) - bx)
        dys.append(float(actual[ref]["y"]) - by)
    tx = statistics.median(dxs)
    ty = statistics.median(dys)

    residuals: list[float] = []
    for ref in refs:
        bx, by = transformed_xy(baseline[ref], mode)
        ex = bx + tx
        ey = by + ty
        residuals.append(math.hypot(float(actual[ref]["x"]) - ex, float(actual[ref]["y"]) - ey))

    rms = math.sqrt(sum(v * v for v in residuals) / max(1, len(residuals)))
    return {
        "mode": mode,
        "translation_x_mm": tx,
        "translation_y_mm": ty,
        "rms_error_mm": rms,
        "max_error_mm": max(residuals, default=0.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bom", type=Path, help="JLCEDA/JLCPCB CSV BOM to verify")
    parser.add_argument("--cpl", type=Path, help="JLCEDA/JLCPCB CSV pick-and-place file to verify")
    parser.add_argument("--self-test", action="store_true", help="verify the frozen source BOM/CPL through the same round-trip parser")
    parser.add_argument("--xy-tolerance-mm", type=float, default=0.02)
    parser.add_argument("--rotation-tolerance-deg", type=float, default=0.1)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if args.self_test:
        actual_bom_path = BASE_BOM
        actual_cpl_path = BASE_CPL
    else:
        if args.bom is None or args.cpl is None:
            parser.error("--bom and --cpl are required unless --self-test is used")
        actual_bom_path = args.bom.resolve()
        actual_cpl_path = args.cpl.resolve()

    failures: list[str] = []
    warnings: list[str] = []

    try:
        base_bom = flatten_bom(read_csv(BASE_BOM), "baseline")
        base_cpl = flatten_cpl(read_csv(BASE_CPL), "baseline")
        actual_bom = flatten_bom(read_csv(actual_bom_path), "JLC export")
        actual_cpl = flatten_cpl(read_csv(actual_cpl_path), "JLC export")
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1

    expected_assembled = set(base_bom)
    actual_assembled = set(actual_bom)

    leaked_dnp = sorted(DNP_REFS & actual_assembled)
    if leaked_dnp:
        failures.append(f"DNP reference(s) leaked into JLC BOM: {', '.join(leaked_dnp)}")

    missing_bom = sorted(expected_assembled - actual_assembled)
    unexpected_bom = sorted(actual_assembled - expected_assembled)
    if missing_bom:
        failures.append(f"JLC BOM missing assembled reference(s): {', '.join(missing_bom)}")
    if unexpected_bom:
        failures.append(f"JLC BOM contains unexpected reference(s): {', '.join(unexpected_bom)}")

    for ref in sorted(expected_assembled & actual_assembled):
        expected_lcsc = base_bom[ref]["lcsc"]
        actual_lcsc = actual_bom[ref]["lcsc"]
        if expected_lcsc != actual_lcsc:
            failures.append(f"{ref}: LCSC mismatch: expected {expected_lcsc}, got {actual_lcsc or '<blank>'}")

    missing_cpl = sorted(expected_assembled - set(actual_cpl))
    if missing_cpl:
        failures.append(f"JLC CPL missing assembled reference(s): {', '.join(missing_cpl)}")

    baseline_cpl_refs = set(base_cpl)
    unexpected_cpl = sorted(set(actual_cpl) - baseline_cpl_refs - CPL_ONLY_ALLOWED)
    if unexpected_cpl:
        failures.append(f"JLC CPL contains unexpected reference(s): {', '.join(unexpected_cpl)}")

    compare_refs = sorted(expected_assembled & set(actual_cpl) & set(base_cpl))
    geometry_fit: dict[str, float | str] | None = None
    if compare_refs:
        candidates = [
            fit_mode(base_cpl, actual_cpl, compare_refs, "identity"),
            fit_mode(base_cpl, actual_cpl, compare_refs, "mirror-y-coordinate-frame"),
        ]
        geometry_fit = min(candidates, key=lambda item: float(item["rms_error_mm"]))
        mode = str(geometry_fit["mode"])
        tx = float(geometry_fit["translation_x_mm"])
        ty = float(geometry_fit["translation_y_mm"])

        for ref in compare_refs:
            bx, by = transformed_xy(base_cpl[ref], mode)
            expected_x = bx + tx
            expected_y = by + ty
            error = math.hypot(
                float(actual_cpl[ref]["x"]) - expected_x,
                float(actual_cpl[ref]["y"]) - expected_y,
            )
            if error > args.xy_tolerance_mm:
                failures.append(f"{ref}: placement drift {error:.4f} mm exceeds {args.xy_tolerance_mm:.4f} mm")

            expected_layer = str(base_cpl[ref]["layer"])
            actual_layer = str(actual_cpl[ref]["layer"])
            if expected_layer != actual_layer:
                failures.append(f"{ref}: layer changed: expected {expected_layer}, got {actual_layer}")

            expected_rotation = transformed_rotation(float(base_cpl[ref]["rotation"]), mode)
            actual_rotation = float(actual_cpl[ref]["rotation"]) % 360.0
            rot_error = angle_error(actual_rotation, expected_rotation)
            if rot_error > args.rotation_tolerance_deg:
                failures.append(
                    f"{ref}: rotation changed: expected {expected_rotation:.3f} deg in {mode}, "
                    f"got {actual_rotation:.3f} deg (error {rot_error:.3f})"
                )

        if mode != "identity":
            warnings.append(
                "CPL best-fit uses mirror-y-coordinate-frame. This can be a harmless exporter handedness change, "
                "but Gerber/board-outline alignment and polarized-part preview must be checked before ordering."
            )
    else:
        failures.append("no common assembled references available for CPL geometry comparison")

    report = {
        "schema": 1,
        "baseline_bom": str(BASE_BOM.relative_to(ROOT)),
        "baseline_cpl": str(BASE_CPL.relative_to(ROOT)),
        "actual_bom": str(actual_bom_path),
        "actual_cpl": str(actual_cpl_path),
        "assembled_reference_count": len(expected_assembled),
        "compared_cpl_reference_count": len(compare_refs),
        "dnp_refs": sorted(DNP_REFS),
        "geometry_fit": geometry_fit,
        "xy_tolerance_mm": args.xy_tolerance_mm,
        "rotation_tolerance_deg": args.rotation_tolerance_deg,
        "warnings": warnings,
        "failures": failures,
        "result": "pass" if not failures else "fail",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"SPINC JLC export round-trip: {report['result'].upper()}")
    print(f"assembled refs: {len(expected_assembled)}, compared CPL refs: {len(compare_refs)}")
    if geometry_fit:
        print(
            "coordinate fit: "
            f"{geometry_fit['mode']}, offset=({float(geometry_fit['translation_x_mm']):.4f}, "
            f"{float(geometry_fit['translation_y_mm']):.4f}) mm, "
            f"max residual={float(geometry_fit['max_error_mm']):.4f} mm"
        )
    for item in warnings:
        print(f"WARN: {item}")
    for item in failures:
        print(f"FAIL: {item}")
    print(f"report: {args.report}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
