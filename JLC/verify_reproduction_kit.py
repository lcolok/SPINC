#!/usr/bin/env python3
"""Verify the maker-facing SPINC Golden Rev A reproduction inventory.

This is a source/inventory gate only. It does not build firmware, run live
JLCEDA, order hardware, or qualify a physical charger.
"""
from __future__ import annotations

import configparser
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT_PATH = ROOT / "JLC/reproduction-kit.json"

EXPECTED_STLS = {
    "CAD/Arm.stl",
    "CAD/Button.stl",
    "CAD/Connector Pin.stl",
    "CAD/Frontpanel.stl",
    "CAD/Shell L.stl",
    "CAD/Shell R.stl",
}
EXPECTED_OFFBOARD = {
    "display": {"quantity": 1, "manufacturer": "Sharp", "mpn": "LS027B7DH01A"},
    "servo": {"quantity": 1, "manufacturer": "EMAX", "mpn": "ES08A"},
    "m3x5-screw": {"quantity": 2, "spec": "M3x5"},
}
BOOT_TRACE = (
    "327GND              SW2   -1",
    "327NET-(R2-PAD2)    SW2   -2",
    "327QSPI_CS          R2    -1",
    "327NET-(R2-PAD2)    R2    -2",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(data, dict), f"{path} must contain a JSON object")
    return data


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    require(candidate.is_relative_to(root.resolve()), f"path escapes repository: {relative}")
    require(candidate.is_file(), f"required reproduction asset missing: {relative}")
    return candidate


def verify(root: Path = ROOT, kit_path: Path | None = None) -> dict:
    path = kit_path or root / "JLC/reproduction-kit.json"
    kit = load_json(path)
    require(kit.get("schema") == 1, "unsupported reproduction-kit schema")

    baseline = load_json(local_file(root, kit["contracts"]["baseline"]))
    require(
        baseline.get("upstream", {}).get("commit") == kit["upstream"]["commit"],
        "reproduction kit and Rev A baseline disagree on upstream commit",
    )

    pcb = kit["pcb"]
    for key in ("schematic", "board", "productionBom", "productionPositions", "productionNetlist"):
        local_file(root, pcb[key])

    bindings = load_json(local_file(root, kit["contracts"]["bindingManifest"]))
    require(bindings.get("componentCount") == pcb["expectedPopulatedDesignators"], "populated designator count drift")
    require(bindings.get("uniqueLcscCount") == pcb["expectedUniqueLcscParts"], "unique LCSC part count drift")
    require(bindings.get("dnpReferences") == pcb["dnpReferences"], "DNP reference set drift")
    u8 = [item for item in bindings.get("bindings", []) if "U8" in item.get("references", [])]
    require(len(u8) == 1, "U8 must resolve to exactly one binding")
    require(u8[0].get("lcsc") == "C7455651", "U8 LCSC identity drift")
    require(u8[0].get("sourceValues") == ["DS2712E+"], "U8 charger identity drift")

    mechanical = kit["mechanical"]
    declared_stls = set(mechanical["files"])
    require(declared_stls == EXPECTED_STLS, "Rev A STL declaration drift")
    declared_hashes = mechanical.get("sha256", {})
    require(set(declared_hashes) == declared_stls, "Rev A STL hash manifest must cover every declared STL exactly")
    for relative in sorted(declared_stls):
        asset = local_file(root, relative)
        require(sha256(asset) == declared_hashes[relative], f"CAD STL content drift: {relative}")
    if mechanical.get("strictStlInventory"):
        actual = {p.relative_to(root).as_posix() for p in (root / mechanical["directory"]).glob("*.stl")}
        require(actual == declared_stls, "CAD STL inventory differs from frozen Rev A kit")

    firmware = kit["firmware"]
    config_path = local_file(root, firmware["config"])
    local_file(root, firmware["entrypoint"])
    local_file(root, firmware["fontAsset"])
    contract_path = local_file(root, firmware["contract"])
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(contract.get("schema") == 1, "firmware contract schema drift")
    require(contract.get("environment", {}).get("name") == firmware["environment"], "firmware contract environment drift")
    require(contract.get("officialBuild", {}).get("entrypoint") == firmware.get("buildCommand"), "firmware build command drift")
    require(contract.get("officialBuild", {}).get("remoteCommand") == firmware.get("remoteBuildCommand"), "firmware remote build command drift")
    expected_uf2 = contract.get("expectedArtifacts", {}).get("pico-firmware.uf2", {}).get("sha256")
    require(expected_uf2 == firmware.get("expectedUf2Sha256"), "firmware Golden UF2 digest drift")
    flash = firmware.get("flash", {})
    require(flash.get("method") == "rp2040-bootsel-mass-storage-uf2", "firmware flash method drift")
    require(flash.get("artifact") == "pico-firmware.uf2", "firmware flash artifact drift")
    require(flash.get("artifactSha256") == expected_uf2, "firmware flash artifact digest drift")
    require(flash.get("qualification") == "physical-step-not-performed-by-aid-build", "firmware flash qualification drift")
    require("uploadCommand" not in firmware, "remote build contract must not masquerade as a device upload command")
    build_command = firmware.get("buildCommand", [])
    require(len(build_command) == 2 and build_command[0] == "python3", "firmware build entrypoint must remain governed Python wrapper")
    local_file(root, build_command[1])

    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path, encoding="utf-8")
    section = f"env:{firmware['environment']}"
    require(config.has_section(section), f"missing PlatformIO environment {section}")
    require(config.get(section, "board", fallback="").strip() == "pico", "PlatformIO board drift")
    require(config.get(section, "framework", fallback="").strip() == "arduino", "PlatformIO framework drift")

    offboard = {item["id"]: item for item in kit["offboardParts"]}
    require(set(offboard) == set(EXPECTED_OFFBOARD), "off-board BOM identity set drift")
    for item_id, expected in EXPECTED_OFFBOARD.items():
        for key, value in expected.items():
            require(offboard[item_id].get(key) == value, f"off-board BOM drift: {item_id}.{key}")

    netlist = local_file(root, pcb["productionNetlist"]).read_text(encoding="utf-8", errors="replace")
    for fragment in BOOT_TRACE:
        require(fragment in netlist, f"BOOTSEL trace missing from netlist: {fragment}")

    with local_file(root, pcb["productionBom"]).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    r2 = [row for row in rows if row.get("Designator", "").strip() == "R2"]
    require(len(r2) == 1 and r2[0].get("Value", "").strip() == "1k", "BOOTSEL series resistor R2 must remain 1k")

    gates = kit.get("physicalGoldenGates", [])
    require(len(gates) == len(set(gates)) and len(gates) >= 10, "physical Golden gate list is incomplete or duplicated")

    return {
        "result": "passed",
        "qualification": "source-inventory-only-not-firmware-build-manufacturing-or-physical-golden",
        "upstream_commit": kit["upstream"]["commit"],
        "pcb_populated_designators": bindings["componentCount"],
        "pcb_unique_lcsc_parts": bindings["uniqueLcscCount"],
        "stl_files": len(declared_stls),
        "offboard_part_lines": len(offboard),
        "platformio_environment": firmware["environment"],
        "firmware_contract": firmware["contract"],
        "golden_uf2_sha256": firmware["expectedUf2Sha256"],
        "flash_method": firmware["flash"]["method"],
        "bootsel_trace": "SW2 -> R2(1k) -> QSPI_CS; SW2 other side -> GND",
        "physical_gates": len(gates),
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
