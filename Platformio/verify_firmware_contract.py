#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INI_PATH = ROOT / "Platformio" / "platformio.ini"
CONTRACT_PATH = ROOT / "Platformio" / "firmware-build-contract.json"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
FLOATING_TOKENS = ("^", "~", ">", "<", "*", "#main", "#master", "#develop", "#development")


def fail(message: str) -> None:
    raise ValueError(message)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        fail("unsupported firmware contract schema")
    return data


def multiline(config: configparser.ConfigParser, section: str, key: str) -> list[str]:
    raw = config.get(section, key, fallback="")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def reject_floating(spec: str, label: str) -> None:
    lower = spec.lower()
    for token in FLOATING_TOKENS:
        if token in lower:
            fail(f"{label} contains floating token {token!r}: {spec}")
    if ".git" in lower:
        if "#" not in spec:
            fail(f"{label} git source is not commit-pinned: {spec}")
        ref = spec.rsplit("#", 1)[1].strip()
        if not FULL_SHA.fullmatch(ref):
            fail(f"{label} git ref is not a full 40-hex commit: {spec}")


def verify_static(root: Path = ROOT, ini_path: Path | None = None, contract_path: Path | None = None) -> dict:
    ini = ini_path or root / "Platformio" / "platformio.ini"
    contract_file = contract_path or root / "Platformio" / "firmware-build-contract.json"
    contract = load_contract(contract_file)

    config = configparser.ConfigParser(interpolation=None)
    config.read(ini, encoding="utf-8")
    section = f"env:{contract['environment']['name']}"
    if not config.has_section(section):
        fail(f"missing [{section}]")

    env = contract["environment"]
    if config.get(section, "board", fallback="").strip() != env["board"]:
        fail("board drift")
    if config.get(section, "framework", fallback="").strip() != env["framework"]:
        fail("framework drift")
    if config.get(section, "board_build.core", fallback="").strip() != env["boardBuildCore"]:
        fail("board_build.core drift")

    platform = config.get(section, "platform", fallback="").strip()
    if platform != contract["platform"]["spec"]:
        fail(f"platform drift: {platform!r}")
    reject_floating(platform, "platform")
    if contract["platform"].get("commit") and contract["platform"]["commit"] not in platform:
        fail("platform commit missing from platform spec")

    actual_platform_packages = multiline(config, section, "platform_packages")
    expected_platform_packages = [item["spec"] for item in contract["platformPackages"]]
    if actual_platform_packages != expected_platform_packages:
        fail(f"platform_packages drift: {actual_platform_packages!r}")
    for idx, spec in enumerate(actual_platform_packages):
        reject_floating(spec, f"platform_packages[{idx}]")

    actual_libs = multiline(config, section, "lib_deps")
    expected_libs = [item["spec"] for item in contract["libraries"]]
    if actual_libs != expected_libs:
        fail(f"lib_deps drift: {actual_libs!r}")
    for idx, spec in enumerate(actual_libs):
        reject_floating(spec, f"lib_deps[{idx}]")
        if "@" in spec and "git" not in spec.lower():
            version = spec.rsplit("@", 1)[1].strip()
            if any(ch in version for ch in "^~><*"):
                fail(f"lib_deps[{idx}] is not exact: {spec}")

    official = contract["officialBuild"]
    if official["transport"] != "aid-remote-platformio":
        fail("official transport must remain aid-remote-platformio")
    if official["workerOS"] != "linux" or official["workerArch"] != "amd64":
        fail("Golden Rev A build worker target must remain linux/amd64")
    if official.get("entrypoint") != ["python3", "Platformio/golden_build.py"]:
        fail("official firmware entrypoint drift")
    if official.get("remoteCommand") != ["pio", "run", "-d", "Platformio", "-e", "pico"]:
        fail("official remote build command drift")
    for item in contract["platformPackages"]:
        digest = item.get("archiveSha256")
        if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail(f"invalid archiveSha256 for {item.get('name')}")

    return {
        "result": "passed",
        "qualification": "static-build-contract",
        "platform_commit": contract["platform"]["commit"],
        "platform_packages": len(actual_platform_packages),
        "libraries": len(actual_libs),
        "expected_uf2_sha256": contract["expectedArtifacts"]["pico-firmware.uf2"]["sha256"],
    }


def verify_runtime(contract: dict) -> dict:
    pio = shutil.which("pio")
    if not pio:
        fail("pio not found in PATH")
    marker_found = False
    try:
        raw = Path(pio).read_text(encoding="utf-8", errors="ignore")
        marker_found = "AID_REMOTE_EMBEDDED_SHIM_V1" in raw
    except OSError:
        pass
    if not marker_found:
        fail(f"official pio entrypoint is not the AID remote shim: {pio}")

    version = subprocess.run(["pio", "--version"], text=True, capture_output=True, check=True).stdout.strip()
    expected = f"PlatformIO Core, version {contract['officialBuild']['platformioCore']}"
    if version != expected:
        fail(f"remote PlatformIO Core drift: {version!r}, want {expected!r}")

    workers_proc = subprocess.run(
        ["aidctl", "--json", "build", "workers"],
        text=True,
        capture_output=True,
        check=True,
    )
    capabilities = json.loads(workers_proc.stdout)
    required_features = set(contract["officialBuild"]["requiredFeatures"])
    matches = []
    for cap in capabilities:
        if cap.get("os") != contract["officialBuild"]["workerOS"] or cap.get("arch") != contract["officialBuild"]["workerArch"]:
            continue
        if cap.get("toolchains", {}).get("platformio") != expected:
            continue
        if not required_features.issubset(set(cap.get("features", []))):
            continue
        matches.append(cap.get("worker_name"))
    if not matches:
        fail("no fresh Linux/amd64 AID worker satisfies the firmware build contract")

    return {
        "pio": pio,
        "platformio": version,
        "compatible_workers": matches,
    }


def verify_artifacts(root: Path, contract: dict) -> dict:
    build_dir = root / "Platformio" / ".pio" / "build" / contract["environment"]["name"]
    checked = {}
    for name, expected in contract["expectedArtifacts"].items():
        local_name = name.removeprefix("pico-")
        path = build_dir / local_name
        if not path.is_file():
            fail(f"artifact missing: {path}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected["sha256"]:
            fail(f"artifact sha drift for {name}: {digest}")
        if len(data) != expected["bytes"]:
            fail(f"artifact size drift for {name}: {len(data)}")
        checked[name] = {"sha256": digest, "bytes": len(data)}
    return checked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", action="store_true", help="also verify the installed AID remote PlatformIO path")
    parser.add_argument("--artifacts", action="store_true", help="also verify restored .pio/build/pico artifacts")
    args = parser.parse_args()

    contract = load_contract()
    result = verify_static()
    if args.runtime:
        result["runtime"] = verify_runtime(contract)
    if args.artifacts:
        result["artifacts"] = verify_artifacts(ROOT, contract)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
