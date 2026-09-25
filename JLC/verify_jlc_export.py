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
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
BASE_BOM = ROOT / "PCB" / "SPINC AA Charger" / "production" / "bom.csv"
BASE_CPL = ROOT / "PCB" / "SPINC AA Charger" / "production" / "positions.csv"
DEFAULT_REPORT = ROOT / "JLC" / "out" / "jlc-export-verification.json"

DNP_REFS = {"TH3"}
CPL_ONLY_ALLOWED = {"TH3", "FID1", "FID2", "FID3", "FID4"}

BOM_QUANTITY_COLUMNS = ("Quantity", "Qty")

BOM_DESIGNATOR_COLUMNS = ("Designator", "Reference", "References", "Ref", "Ref Des")
BOM_LCSC_COLUMNS = (
    "LCSC Part #",
    "LCSC Part Number",
    "JLCPCB Part #",
    "JLCPCB Part Number",
    "JLC Part #",
    "Supplier Part",
    "Supplier Part #",
)
CPL_DESIGNATOR_COLUMNS = BOM_DESIGNATOR_COLUMNS
CPL_X_COLUMNS = ("Mid X", "Mid X (mm)", "MidX", "X", "X (mm)", "Mid X (mil)", "X (mil)")
CPL_Y_COLUMNS = ("Mid Y", "Mid Y (mm)", "MidY", "Y", "Y (mm)", "Mid Y (mil)", "Y (mil)")
CPL_ROT_COLUMNS = ("Rotation", "Rot", "Rotation (deg)")
CPL_LAYER_COLUMNS = ("Layer", "Side")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        headers = reader.fieldnames or []
        normalized = [h.strip().lower() for h in headers]
        if not headers or any(not h for h in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError(f"{path.name}: empty or duplicate CSV headers")
        rows = list(reader)
        for number, row in enumerate(rows, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path.name}: row {number} does not match the CSV header width")
        return rows


def first_column(headers: Iterable[str], aliases: tuple[str, ...], label: str) -> str:
    normalized = {h.strip().lower(): h for h in headers if h is not None}
    matches = [normalized[alias.lower()] for alias in aliases if alias.lower() in normalized]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {label} column; found {matches}")
    return matches[0]


def refs_from_cell(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    # Accept explicit comma/semicolon/space lists, never silently discard a
    # range, suffix, or malformed extra reference from a manufacturing file.
    refs = [item for item in re.split(r"[,;\s]+", text) if item]
    if not refs or any(not re.fullmatch(r"[A-Za-z]+[0-9]+", item) for item in refs):
        raise ValueError(f"invalid designator list: {value!r}")
    return [item.upper() for item in refs]


def normalize_lcsc(value: str) -> str:
    text = (value or "").strip().upper()
    if not re.fullmatch(r"C[0-9]+", text):
        raise ValueError(f"expected exactly one LCSC/JLC C-number, got {value!r}")
    return text


def finite_number(value: str, label: str) -> float:
    text = (value or "").strip()
    # float() alone accepts NaN/Inf, which can bypass greater-than tolerance
    # comparisons. Empty angles must not silently become a valid zero.
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
        raise ValueError(f"{label}: expected a finite number, got {value!r}")
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"{label}: non-finite number")
    return number


def parse_mm(value: str, column_unit: str | None = None) -> float:
    text = (value or "").strip().lower()
    match = re.fullmatch(r"(.*?)\s*(mm|mil)?", text)
    if match is None:
        raise ValueError(f"invalid coordinate: {value!r}")
    number = finite_number(match.group(1), "coordinate")
    suffix = match.group(2)
    if column_unit and suffix and column_unit != suffix:
        raise ValueError(f"coordinate suffix {suffix} contradicts {column_unit} header")
    unit = suffix or column_unit or "mm"
    if unit not in {"mm", "mil"}:
        raise ValueError(f"unsupported coordinate unit: {unit}")
    result = number * (0.0254 if unit == "mil" else 1.0)
    if not math.isfinite(result):
        raise ValueError("non-finite coordinate after unit conversion")
    return result


def column_unit(header: str) -> str | None:
    match = re.search(r"\((mm|mil)\)$", header.strip(), re.IGNORECASE)
    return match.group(1).lower() if match else None


def normalize_layer(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"t", "top", "front", "f", "topsurface", "toplayer"}:
        return "top"
    if text in {"b", "bottom", "back", "rear", "bottomsurface", "bottomlayer"}:
        return "bottom"
    raise ValueError(f"unknown or missing assembly side: {value!r}")


def write_report(path: Path, report: dict[str, object]) -> None:
    """Atomically replace previous evidence; never serialize NaN/Infinity."""
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def angle_error(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def flatten_bom(rows: list[dict[str, str]], source_name: str) -> dict[str, dict[str, str]]:
    if not rows:
        raise ValueError(f"{source_name}: BOM is empty")
    headers = rows[0].keys()
    designator_col = first_column(headers, BOM_DESIGNATOR_COLUMNS, "BOM designator")
    lcsc_col = first_column(headers, BOM_LCSC_COLUMNS, "BOM LCSC/JLC part number")
    quantity_col = None
    if any(h.strip().lower() in {a.lower() for a in BOM_QUANTITY_COLUMNS} for h in headers):
        quantity_col = first_column(headers, BOM_QUANTITY_COLUMNS, "BOM quantity")

    result: dict[str, dict[str, str]] = {}
    for row in rows:
        refs = refs_from_cell(row.get(designator_col, ""))
        if not refs:
            if any(str(value or "").strip() for value in row.values()):
                raise ValueError(f"{source_name}: nonempty row has no designator")
            continue
        lcsc = normalize_lcsc(row.get(lcsc_col, ""))
        if quantity_col is not None:
            quantity = (row.get(quantity_col, "") or "").strip()
            if not re.fullmatch(r"[0-9]+", quantity) or int(quantity) != len(refs):
                raise ValueError(f"{source_name}: quantity {quantity!r} does not match {len(refs)} designators")
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
            if any(str(value or "").strip() for value in row.values()):
                raise ValueError(f"{source_name}: nonempty row has no designator")
            continue
        if len(refs) != 1:
            raise ValueError(f"{source_name}: CPL row must contain exactly one designator, got {refs}")
        ref = refs[0]
        if ref in result:
            raise ValueError(f"{source_name}: duplicate CPL designator {ref}")
        result[ref] = {
            "x": parse_mm(row.get(x_col, ""), column_unit(x_col)),
            "y": parse_mm(row.get(y_col, ""), column_unit(y_col)),
            "rotation": finite_number(row.get(rot_col, ""), f"{source_name} {ref} rotation"),
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

    if any(not math.isfinite(value) for value in [tx, ty, *residuals]):
        raise ValueError("non-finite coordinate-fit arithmetic")
    scale = max(residuals, default=0.0)
    rms = scale * math.sqrt(sum((value / scale) ** 2 for value in residuals) / len(residuals)) if scale else 0.0
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
    parser.add_argument("--xy-tolerance-mm", default="0.02")
    parser.add_argument("--rotation-tolerance-deg", default="0.1")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    input_paths = [BASE_BOM, BASE_CPL, *[p for p in (args.bom, args.cpl) if p is not None]]
    if args.report.resolve() in {p.resolve() for p in input_paths}:
        print("FAIL: report path must not overwrite a BOM/CPL input")
        return 1

    if args.self_test:
        actual_bom_path = BASE_BOM
        actual_cpl_path = BASE_CPL
    else:
        if args.bom is None or args.cpl is None:
            write_report(args.report, {"schema": 1, "result": "fail", "phase": "arguments",
                                       "failures": ["--bom and --cpl are required unless --self-test is used"]})
            print("FAIL: --bom and --cpl are required unless --self-test is used")
            return 1
        actual_bom_path = args.bom.resolve()
        actual_cpl_path = args.cpl.resolve()

    failures: list[str] = []
    warnings: list[str] = []
    # An interrupted or malformed new evaluation must not leave an older PASS
    # at the well-known report path for later evidence collection.
    input_failure = {
        "schema": 1, "result": "fail", "phase": "input-validation",
        "qualification": "bom-cpl-only-not-manufacturing-or-golden",
        "actual_bom": str(actual_bom_path), "actual_cpl": str(actual_cpl_path),
        "failures": ["verification has not completed"],
    }
    write_report(args.report, input_failure)
    try:
        args.xy_tolerance_mm = finite_number(args.xy_tolerance_mm, "XY tolerance")
        args.rotation_tolerance_deg = finite_number(args.rotation_tolerance_deg, "rotation tolerance")
        if args.xy_tolerance_mm < 0 or args.rotation_tolerance_deg < 0:
            raise ValueError("tolerances must be non-negative")
        base_bom = flatten_bom(read_csv(BASE_BOM), "baseline")
        base_cpl = flatten_cpl(read_csv(BASE_CPL), "baseline")
        actual_bom = flatten_bom(read_csv(actual_bom_path), "JLC export")
        actual_cpl = flatten_cpl(read_csv(actual_cpl_path), "JLC export")
    except (OSError, ValueError, csv.Error) as exc:
        input_failure["failures"] = [str(exc)]
        write_report(args.report, input_failure)
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
        try:
            candidates = [
                fit_mode(base_cpl, actual_cpl, compare_refs, "identity"),
                fit_mode(base_cpl, actual_cpl, compare_refs, "mirror-y-coordinate-frame"),
            ]
        except ValueError as exc:
            input_failure["failures"] = [str(exc)]
            write_report(args.report, input_failure)
            print(f"FAIL: {exc}")
            return 1
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
        "qualification": "bom-cpl-only-not-manufacturing-or-golden",
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
    write_report(args.report, report)

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
