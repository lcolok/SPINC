#!/usr/bin/env python3
"""Validate the repository's execution contract, without running JLCEDA."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "lcolok/jlc-eda-research"
# The harness source stays private. Consumers pin the exact bytes of a binary
# built from `commit` by the harness repository's reproducible builder, and
# download them from this public repository's release. The SHA-256 below is
# the trust anchor; the download location is not trusted.
RELEASE_REPOSITORY = "lcolok/SPINC"
BINARY_PLATFORM = "linux/amd64"
BINARY_ASSET = "jlc-linux-amd64"
BINARY_BUILDER = ".github/workflows/jlc-harness-binary.yml"
BINARY_ENV = "CGO_ENABLED=0 GOOS=linux GOARCH=amd64"
BINARY_COMMAND = "go build -trimpath -buildvcs=false -ldflags='-s -w -buildid=' ."
REQUIRED_CAPABILITIES = {
    "live runner-once", "flow validate", "flow run --from", "flow status",
    "eda bootstrap --host auto", "project import-external", "activate pcb",
    "pcb drc", "bom export --verify", "pcb export cpl",
    "pcb export gerber", "project export",
}


def release_tag(commit: str) -> str:
    return f"harness-jlc-{commit[:12]}"


def validate_binary(binary: object, commit: str) -> dict[str, str]:
    if not isinstance(binary, dict):
        raise ValueError("binary must be an object")
    expected = {"platform": BINARY_PLATFORM, "asset": BINARY_ASSET,
                "builder": BINARY_BUILDER, "releaseTag": release_tag(commit)}
    for key, value in expected.items():
        if binary.get(key) != value:
            raise ValueError(f"unexpected binary {key}")
    sha = binary.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("binary sha256 must be 64 lowercase hex characters")
    recipe = binary.get("recipe")
    if not isinstance(recipe, dict) or set(recipe) != {"go", "env", "command", "workdir"}:
        raise ValueError("binary recipe must have exactly go, env, command, workdir")
    if not isinstance(recipe["go"], str) or not re.fullmatch(r"1\.[0-9]+\.[0-9]+", recipe["go"]):
        raise ValueError("binary recipe go must be an exact version")
    if (recipe["env"], recipe["command"], recipe["workdir"]) != (BINARY_ENV, BINARY_COMMAND, "jlc"):
        raise ValueError("unexpected binary recipe")
    tag = expected["releaseTag"]
    return {"binary_sha256": sha, "binary_tag": tag,
            "binary_url": f"https://github.com/{RELEASE_REPOSITORY}/releases/download/{tag}/{BINARY_ASSET}"}


def validate_pin(pin: object) -> dict[str, str]:
    if not isinstance(pin, dict):
        raise ValueError("harness pin must be an object")
    if type(pin.get("schema")) is not int or pin["schema"] != 2:
        raise ValueError("unsupported harness pin schema")
    if pin.get("repository") != REPOSITORY:
        raise ValueError("unexpected harness repository")
    commit = pin.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("harness commit must be a full lowercase SHA-1")
    if commit == "0" * 40:
        raise ValueError("zero SHA is not an executable harness pin")
    if pin.get("flowSpec") != "JLC/rev-a/flows/migrate.yaml":
        raise ValueError("unexpected flow spec")
    if pin.get("validationEntryPoint") != "jlc flow validate --board JLC/rev-a --flow migrate":
        raise ValueError("unexpected validation entry point")
    caps = pin.get("requiredCapabilities")
    if not isinstance(caps, list) or any(not isinstance(c, str) for c in caps):
        raise ValueError("requiredCapabilities must be a string list")
    if len(caps) != len(set(caps)):
        raise ValueError("duplicate required capability")
    missing = REQUIRED_CAPABILITIES - set(caps)
    if missing:
        raise ValueError(f"missing required capabilities: {', '.join(sorted(missing))}")
    return {"repository": REPOSITORY, "commit": commit, **validate_binary(pin.get("binary"), commit)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, default=ROOT / "JLC/harness-pin.json")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        result = validate_pin(json.loads(args.pin.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as out:
            for key, value in result.items():
                out.write(f"{key}={value}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
