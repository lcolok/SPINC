# SPINC Golden Rev A firmware

Golden Rev A firmware is built through **AID remote PlatformIO only**. The local
`pio` command is expected to be the governed AID shim; a native local
PlatformIO installation is not an accepted Golden build path.

## One-command build

~~~sh
python3 Platformio/golden_build.py
~~~

The command fails closed in three stages:

1. verify the pinned build contract and the active AID remote runtime;
2. build `env:pico` through the remote Linux/amd64 PlatformIO worker;
3. verify restored UF2/BIN/ELF sizes and SHA-256 values against
   `firmware-build-contract.json`.

For a two-key reproducibility check:

~~~sh
python3 Platformio/golden_build.py --repro-audit
~~~

The audit performs a second remote invocation with PlatformIO verbose mode.
That changes the AID BuildJob parameters (therefore the dedup key) without
changing compiled inputs; both invocations must restore the same Golden artifact
digests. This does **not** prove both invocations executed freshly: AID may reuse
an identical historical terminal cache entry. Fresh-execution evidence requires
recorded job IDs with `cacheHit=false`, as captured in
`firmware-build-contract.json`.

## Frozen build inputs

`platformio.ini` deliberately pins:

- the Raspberry Pi PlatformIO platform to a full Git commit;
- Arduino-Pico to a full Git commit;
- Linux/amd64 GCC, picotool and pioasm to concrete pico-quick-toolchain 5.0.0
  release assets;
- direct and transitive application libraries to exact versions/commits.

Do not replace any of these with caret/range versions, branch names, or floating
Git URLs. `verify_firmware_contract.py` and CI reject those changes.

The current Golden firmware receipt is machine-readable in
`firmware-build-contract.json`. Its qualification is **firmware build only**:
matching bytes do not prove device flashing, charger behavior, mechanics, or a
physical Golden unit.
