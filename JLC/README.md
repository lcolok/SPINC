# SPINC JLC Rev A

This directory governs the first JLC-ecosystem reproduction of the upstream SPINC AA NiMH charger.

## Goal

Produce a **Golden Rev A** that is electrically and mechanically equivalent to upstream SPINC before any custom feature, chemistry expansion, sourcing optimization, cost reduction, topology change, or mechanical redesign.

Pinned upstream source baseline:

- Repository: `CoretechR/SPINC`
- Commit: `af7b36e8ca5e99bfb3e99d8b02d9864117091de7`
- Source EDA: KiCad 8
- Target EDA after accepted migration: **JLCEDA Pro**

The runtime automation is separately pinned in `JLC/harness-pin.json` to the mature Web-first `lcolok/jlc-eda-research` harness. The primary automated entry point is now **`JLC/run_rev_a_harness.sh`**. The standalone JLCEDA JavaScript helpers remain only as a manual fallback; they are not a second maintained production path.

## Rev A manufacturing profile

- PCB / PCBA: JLCPCB
- Layers: 4
- Ordered thickness: 1.6 mm
- Stackup: `JLC04161H-3313`
- Outer copper: 35 um
- Inner copper: approximately 15.2 um
- Surface finish: ENIG
- Solder mask: black
- Silkscreen: white
- Assembly: preserve the upstream top-side population and DNP intent
- PCBA tier: Standard

The selected JLC stackup is intentionally close to the upstream board construction. Rev A does not use the migration as an opportunity to optimize the stackup.

## Frozen component identity

The upstream production BOM remains the source of truth for the populated board. `build_jlc_binding_manifest.py` regenerates `jlc-bindings.json`, and CI rejects a stale binding manifest.

Current frozen population:

- **91 populated designators**
- **44 unique LCSC/JLC C-numbers**
- `TH3` is explicitly **DNP** and may not leak into the assembly BOM
- `U8 = DS2712E+ / C7455651 / TSSOP-16`

For the Golden Rev A, do not replace the DS2712, RP2040, charger power stage, polarity bridge, battery contacts, or other upstream identities merely to improve sourcing or cost.

## Charger topology that must not be reinterpreted

The migration audits deliberately freeze a common source of error:

- `Q5 = SI2305CDS / C37577` is the **main 5 V switching PMOS**.
- `Q4 = DMG2301L / C102619` is the **DS2712 gate/control PMOS**; it is not the main power switch.
- `U8.1 / CC1 -> Q4.G`
- `U8.6 / CSOUT -> Q4.D`
- `Q4.S -> Q5.G`
- `R37 = 330 ohm / C105881` pulls the Q5 gate-control node to 5 V so Q5 defaults off.
- `R36 = 10 kOhm / C98220` biases Q4 gate-to-source.

Frozen charge path:

`5V -> Q5 -> switch node -> L1 (47 uH) -> D4 -> Q2 polarity bridge -> battery -> Q2 low rail -> R34 (0.124 ohm) -> GND`

Supporting parts with frozen identity/orientation include:

- `C30 = 10 uF / C15850`
- `D2 = MBRA340 / C26178` catch diode
- `L1 = SRP7050TA-470M / C2047110`, 47 uH
- `D4 = MBRA340 / C26178` series diode into the H-bridge/VP1 high rail
- `D3 = MMBD4148 / C2928912`; package pin 2 remains explicit NC

`critical-power-stage-audit.json` additionally freezes the upstream XY/rotation/layer of `C30, D2, D3, D4, L1, Q4, Q5, R36, R37, U8`, so an EDA migration cannot preserve connectivity while silently spreading or rotating the switching stage.

## Frozen mechanical references

`rev-a-baseline.json` records critical placement and board geometry. Until an upstream error is proven, the board outline, internal cutouts and mechanical interfaces are immutable.

Particularly sensitive references:

- `J4/J5`: Keystone 590 battery contacts
- `J1`: USB-C
- `J3`: display connector
- `J2`: servo/header interface
- `U7`: VCNL4040 cell detector
- `TH1/TH2`: populated thermistors
- `TH3`: DNP footprint only

## Source-side verification

The static Golden-source gates are:

```bash
python JLC/verify_rev_a.py
python JLC/verify_power_stage.py
python JLC/build_jlc_binding_manifest.py --check
python JLC/verify_jlc_export.py --self-test
bash -n JLC/run_rev_a_harness.sh
node --check JLC/jlceda-scripts/import-rev-a-kicad.js
node --check JLC/jlceda-scripts/audit-jlc-library-bindings.js
node --check JLC/jlceda-scripts/export-rev-a-audit.js
```

Established baseline coverage:

- base reproduction audit: **492 checks passed, 0 failed**
- charger power-stage audit: **164 checks passed, 0 failed**
- total explicit electrical/mechanical equivalence checks: **656**
- binding manifest: 91 populated designators / 44 unique C-numbers
- deterministic KiCad migration ZIP generation
- BOM/CPL round-trip verifier self-test
- shell/standalone helper syntax checks

The source-side CI being green proves the frozen KiCad input and tooling. It does **not** prove that a particular live JLCEDA import is correct.

## Primary runtime path — mature JLC harness

The primary runtime executor is the Web-first `jlc` CLI from the pinned `lcolok/jlc-eda-research` mainline. The pinned harness contains the generic native CPL command:

```bash
jlc pcb export cpl -o positions.csv
```

That capability is implemented through the same mature browser-runtime transport already used by the harness for import/export, rather than a SPINC-specific side channel.

With a logged-in JLCEDA Pro runtime available, run from the SPINC repository root:

```bash
bash JLC/run_rev_a_harness.sh
```

With a previously downloaded CI migration ZIP:

```bash
bash JLC/run_rev_a_harness.sh /path/to/SPINC-JLC-Rev-A-KiCad.zip
```

The runner is deliberately fail-closed and performs these phases in order:

1. rerun the frozen KiCad electrical/mechanical gates and binding-manifest check
2. build or accept the deterministic migration ZIP
3. preflight that the installed `jlc` contains the required native CPL exporter
4. `jlc eda bootstrap --host auto` to reuse the mature Web-first runtime routing
5. `jlc project import-external ... --file-type KiCad` with fail-closed board validation
6. activate the imported PCB, including the harness's standalone-PCB compatibility path
7. run JLCEDA PCB DRC
8. export the migrated BOM as CSV with authoritative JLC identity drift checking
9. export native CPL in millimetres
10. export Gerber and the migrated `.epro2` project
11. round-trip the migrated BOM/CPL against the frozen KiCad BOM/CPL
12. write a SHA-256 runtime-evidence manifest

The round-trip verifier accepts one harmless global coordinate-origin translation and can recognize a coordinate-frame Y inversion, but rejects:

- per-component placement drift
- assembly-side changes
- rotation changes
- missing or extra assembled references
- `TH3` DNP leakage
- C-number changes

The verifier accepts the JLC ecosystem's normal `LCSC Part #` / `JLCPCB Part #` naming as well as the mature harness's native `Supplier Part` BOM column, so no manual CSV editing is part of the production path.

### Important runtime status

**The live SPINC JLCEDA migration has not yet been declared successful.** Repository CI and harness Go tests are green, but Golden Rev A still requires executing `run_rev_a_harness.sh` against the actual logged-in target JLCEDA runtime and reviewing the resulting evidence. Do not interpret source-side CI as a fabricated runtime pass.

## Manual fallback only

If the mature JLC CLI/Bridge cannot be used on the target machine, the following standalone JLCEDA Pro scripts remain available for diagnosis/manual recovery:

- `JLC/jlceda-scripts/import-rev-a-kicad.js`
- `JLC/jlceda-scripts/audit-jlc-library-bindings.js`
- `JLC/jlceda-scripts/export-rev-a-audit.js`

They are intentionally fallback tools. New production logic should go into the shared harness rather than creating a permanent second SPINC-specific automation track.

## Remaining first-board acceptance work

A JLCEDA import is not accepted merely because it opens without an error dialog. Before ordering the first board we still require:

1. execute the primary runtime path against the actual target JLCEDA session
2. capture the importer `projectDataTypes` and resolve any real, observed SCH/PCB metadata incompatibility rather than guessing one in advance
3. review library identity/footprint associations, especially polarized and safety-critical parts
4. verify exact board outline and internal cutouts
5. verify connectors, battery contacts, sensor locations and rotations
6. inspect/rebuild imported copper pours rather than assuming KiCad zones survived identically
7. apply `JLC04161H-3313` and re-check USB routing/return path
8. manually inspect Q5/D2/L1/D4 high-di/dt routing, D3 orientation and R34/R35 current-sense routing
9. verify H-bridge orientation and the `BAT_A/BAT_B` bridge midpoints
10. pass JLCEDA ERC/DRC and JLCPCB DFM review
11. pass BOM/CPL round-trip verification
12. diff Gerber board outline/internal cutout/copper evidence against the Golden source

## Physical Golden-board gates

Rev A is promoted to `golden` only after physical hardware passes:

- USB power and enumeration/programming
- RP2040 clock/flash/boot
- 3.3 V and auxiliary rails
- display interface
- VCNL4040 cell-presence detection
- battery voltage ADC paths
- battery thermistor path
- polarity correction / H-bridge behavior
- DS2712 precharge and fast-charge behavior
- safe termination and error handling
- servo/mechanical loading interface
- exact mechanical fit with the upstream enclosure and battery path

Only after this Golden board exists should Rev B introduce custom improvements.
