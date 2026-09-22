"""Tests for the machine-checkable SPINC reproduction kit."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import verify_reproduction_kit as kit_verify


class ReproductionKitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("JLC", "CAD", "PCB", "Platformio/src"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)

        self.kit = {
            "schema": 1,
            "upstream": {"commit": "a" * 40},
            "contracts": {"baseline": "JLC/rev-a-baseline.json", "bindingManifest": "JLC/jlc-bindings.json"},
            "pcb": {
                "schematic": "PCB/board.kicad_sch",
                "board": "PCB/board.kicad_pcb",
                "productionBom": "PCB/bom.csv",
                "productionPositions": "PCB/positions.csv",
                "productionNetlist": "PCB/netlist.ipc",
                "expectedPopulatedDesignators": 91,
                "expectedUniqueLcscParts": 44,
                "dnpReferences": ["TH3"],
            },
            "mechanical": {
                "directory": "CAD",
                "strictStlInventory": True,
                "files": sorted(kit_verify.EXPECTED_STLS),
            },
            "firmware": {
                "config": "Platformio/platformio.ini",
                "entrypoint": "Platformio/src/main.cpp",
                "fontAsset": "Platformio/src/rubik_140.c",
                "environment": "pico",
                "contract": "Platformio/firmware-build-contract.json",
                "buildCommand": ["python3", "Platformio/golden_build.py"],
                "remoteBuildCommand": ["pio", "run", "-d", "Platformio", "-e", "pico"],
                "expectedUf2Sha256": "d" * 64,
                "flash": {
                    "method": "rp2040-bootsel-mass-storage-uf2",
                    "artifact": "pico-firmware.uf2",
                    "artifactSha256": "d" * 64,
                    "qualification": "physical-step-not-performed-by-aid-build",
                },
            },
            "offboardParts": [
                {"id": "display", "quantity": 1, "manufacturer": "Sharp", "mpn": "LS027B7DH01A"},
                {"id": "servo", "quantity": 1, "manufacturer": "EMAX", "mpn": "ES08A"},
                {"id": "m3x5-screw", "quantity": 2, "spec": "M3x5"},
            ],
            "physicalGoldenGates": [f"gate-{index}" for index in range(10)],
        }

        self.write_json("JLC/rev-a-baseline.json", {"upstream": {"commit": "a" * 40}})
        self.write_json(
            "JLC/jlc-bindings.json",
            {
                "componentCount": 91,
                "uniqueLcscCount": 44,
                "dnpReferences": ["TH3"],
                "bindings": [{"lcsc": "C7455651", "references": ["U8"], "sourceValues": ["DS2712E+"]}],
            },
        )
        for relative in ("PCB/board.kicad_sch", "PCB/board.kicad_pcb", "PCB/positions.csv",
                         "Platformio/src/main.cpp", "Platformio/src/rubik_140.c",
                         "Platformio/golden_build.py"):
            self.write(relative, "fixture\n")
        self.write("PCB/bom.csv", "Designator,Value\nR2,1k\n")
        self.write("PCB/netlist.ipc", "\n".join(kit_verify.BOOT_TRACE) + "\n")
        self.write("Platformio/platformio.ini", "[env:pico]\nboard = pico\nframework = arduino\n")
        self.write_json(
            "Platformio/firmware-build-contract.json",
            {
                "schema": 1,
                "officialBuild": {
                    "entrypoint": ["python3", "Platformio/golden_build.py"],
                    "remoteCommand": ["pio", "run", "-d", "Platformio", "-e", "pico"],
                },
                "environment": {"name": "pico"},
                "expectedArtifacts": {"pico-firmware.uf2": {"sha256": "d" * 64}},
            },
        )
        for relative in kit_verify.EXPECTED_STLS:
            self.write(relative, "solid fixture\nendsolid fixture\n")
        self.kit["mechanical"]["sha256"] = {
            relative: hashlib.sha256((self.root / relative).read_bytes()).hexdigest()
            for relative in kit_verify.EXPECTED_STLS
        }
        self.write_json("JLC/reproduction-kit.json", self.kit)

    def write(self, relative: str, data: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")

    def write_json(self, relative: str, data: dict) -> None:
        self.write(relative, json.dumps(data))

    def verify(self):
        return kit_verify.verify(self.root, self.root / "JLC/reproduction-kit.json")

    def test_valid_fixture_passes(self):
        result = self.verify()
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["stl_files"], 6)

    def test_missing_stl_fails_closed(self):
        (self.root / "CAD/Arm.stl").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.verify()

    def test_stl_content_drift_fails_closed(self):
        self.write("CAD/Arm.stl", "changed geometry bytes\n")
        with self.assertRaisesRegex(ValueError, "STL content drift"):
            self.verify()

    def test_bootsel_trace_drift_fails_closed(self):
        self.write("PCB/netlist.ipc", "unrelated netlist\n")
        with self.assertRaisesRegex(ValueError, "BOOTSEL"):
            self.verify()

    def test_u8_identity_drift_fails_closed(self):
        bindings = json.loads((self.root / "JLC/jlc-bindings.json").read_text())
        bindings["bindings"][0]["lcsc"] = "C000000"
        self.write_json("JLC/jlc-bindings.json", bindings)
        with self.assertRaisesRegex(ValueError, "U8 LCSC"):
            self.verify()

    def test_offboard_bom_drift_fails_closed(self):
        changed = copy.deepcopy(self.kit)
        changed["offboardParts"][1]["mpn"] = "generic-servo"
        self.write_json("JLC/reproduction-kit.json", changed)
        with self.assertRaisesRegex(ValueError, "off-board BOM"):
            self.verify()

    def test_firmware_contract_digest_drift_fails_closed(self):
        changed = copy.deepcopy(self.kit)
        changed["firmware"]["expectedUf2Sha256"] = "e" * 64
        self.write_json("JLC/reproduction-kit.json", changed)
        with self.assertRaisesRegex(ValueError, "Golden UF2 digest"):
            self.verify()

    def test_remote_upload_command_is_rejected(self):
        changed = copy.deepcopy(self.kit)
        changed["firmware"]["uploadCommand"] = ["pio", "run", "-t", "upload"]
        self.write_json("JLC/reproduction-kit.json", changed)
        with self.assertRaisesRegex(ValueError, "masquerade"):
            self.verify()

    def test_flash_digest_must_match_golden_artifact(self):
        changed = copy.deepcopy(self.kit)
        changed["firmware"]["flash"]["artifactSha256"] = "e" * 64
        self.write_json("JLC/reproduction-kit.json", changed)
        with self.assertRaisesRegex(ValueError, "flash artifact digest"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
