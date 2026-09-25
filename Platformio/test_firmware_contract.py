from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import verify_firmware_contract as verifier


class RepositoryFirmwareContractTests(unittest.TestCase):
    def test_repository_contract_is_pinned(self):
        result = verifier.verify_static()
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["platform_packages"], 4)
        self.assertEqual(result["libraries"], 5)


class FirmwareContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Platformio").mkdir(parents=True)
        self.contract = {
            "schema": 1,
            "officialBuild": {
                "transport": "aid-remote-platformio",
                "entrypoint": ["python3", "Platformio/golden_build.py"],
                "remoteCommand": ["pio", "run", "-d", "Platformio", "-e", "pico"],
                "workerOS": "linux",
                "workerArch": "amd64",
                "platformioCore": "6.1.19",
                "requiredFeatures": ["embedded-command-v1", "workspace-snapshot-v1"],
            },
            "environment": {
                "name": "pico",
                "board": "pico",
                "framework": "arduino",
                "boardBuildCore": "earlephilhower",
            },
            "platform": {
                "spec": "https://example.test/platform.git#" + "a" * 40,
                "commit": "a" * 40,
            },
            "platformPackages": [
                {"spec": "framework-x @ https://example.test/framework.git#" + "b" * 40}
            ],
            "libraries": [
                {"spec": "vendor/library@1.2.3"},
                {"spec": "https://example.test/library.git#" + "c" * 40},
            ],
            "expectedArtifacts": {
                "pico-firmware.uf2": {"sha256": "d" * 64, "bytes": 1}
            },
        }
        self.write_contract(self.contract)
        self.write_ini(
            "[env:pico]\n"
            "platform = " + self.contract["platform"]["spec"] + "\n"
            "board = pico\n"
            "framework = arduino\n"
            "board_build.core = earlephilhower\n"
            "platform_packages =\n"
            "  " + self.contract["platformPackages"][0]["spec"] + "\n"
            "lib_deps =\n"
            "  vendor/library@1.2.3\n"
            "  https://example.test/library.git#" + "c" * 40 + "\n"
        )

    def write_contract(self, value):
        (self.root / "Platformio/firmware-build-contract.json").write_text(json.dumps(value), encoding="utf-8")

    def write_ini(self, value):
        (self.root / "Platformio/platformio.ini").write_text(value, encoding="utf-8")

    def verify(self):
        return verifier.verify_static(
            self.root,
            self.root / "Platformio/platformio.ini",
            self.root / "Platformio/firmware-build-contract.json",
        )

    def test_valid_exact_contract_passes(self):
        self.assertEqual(self.verify()["result"], "passed")

    def test_caret_library_fails_closed(self):
        self.contract["libraries"][0]["spec"] = "vendor/library@^1.2.3"
        self.write_contract(self.contract)
        self.write_ini(
            "[env:pico]\n"
            "platform = " + self.contract["platform"]["spec"] + "\n"
            "board = pico\nframework = arduino\nboard_build.core = earlephilhower\n"
            "platform_packages =\n  " + self.contract["platformPackages"][0]["spec"] + "\n"
            "lib_deps =\n  vendor/library@^1.2.3\n  https://example.test/library.git#" + "c" * 40 + "\n"
        )
        with self.assertRaisesRegex(ValueError, "floating"):
            self.verify()

    def test_git_without_full_commit_fails_closed(self):
        self.contract["platform"]["spec"] = "https://example.test/platform.git#main"
        self.contract["platform"]["commit"] = "main"
        self.write_contract(self.contract)
        self.write_ini(
            "[env:pico]\n"
            "platform = https://example.test/platform.git#main\n"
            "board = pico\nframework = arduino\nboard_build.core = earlephilhower\n"
            "platform_packages =\n  " + self.contract["platformPackages"][0]["spec"] + "\n"
            "lib_deps =\n  vendor/library@1.2.3\n  https://example.test/library.git#" + "c" * 40 + "\n"
        )
        with self.assertRaisesRegex(ValueError, "floating|full 40-hex"):
            self.verify()

    def test_platform_package_order_drift_fails(self):
        self.contract["platformPackages"].append({"spec": "tool-x @ https://example.test/tool.tar.gz"})
        self.write_contract(self.contract)
        with self.assertRaisesRegex(ValueError, "platform_packages drift"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
