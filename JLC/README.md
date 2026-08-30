# SPINC JLC Rev A

This directory tracks the first-pass JLC reproduction of the upstream SPINC AA NiMH charger.

## Goal

Build a **golden Rev A** board that is electrically and mechanically equivalent to upstream SPINC before attempting any custom features, chemistry expansion, cost optimization, or topology changes.

Pinned upstream baseline:

- Repository: `CoretechR/SPINC`
- Commit: `af7b36e8ca5e99bfb3e99d8b02d9864117091de7`
- Source EDA: KiCad 8
- Target EDA SSoT after validated migration: **JLCEDA Pro**

## Rev A manufacturing profile

- PCB vendor: JLCPCB
- Assembly vendor: JLCPCB
- Layers: 4
- Ordered thickness: 1.6 mm
- Stackup: `JLC04161H-3313`
- Outer copper: 35 um
- Inner copper: approximately 15.2 um
- Surface finish: ENIG
- Solder mask: black
- Silkscreen: white
- Assembly: top-side components; preserve upstream DNP intent
- PCBA tier: Standard

The selected JLC stackup is intentionally close to the upstream board's recorded construction. Rev A does not use stackup changes as an optimization opportunity.

## Component policy

For Rev A, preserve upstream electrical identities wherever possible.

The production BOM is the source of truth for populated components. `build_jlc_binding_manifest.py` regenerates `jlc-bindings.json` from that BOM and CI refuses a stale manifest.

Current frozen population:

- **91 populated designators**
- **44 unique LCSC/JLC C-numbers**
- `TH3` remains explicitly **DNP** and is never allowed into the populated-device manifest

The previously-unbound charger controller is explicitly bound as:

- `U8 = DS2712E+`
- Package: 16-pin TSSOP
- LCSC: `C7455651`
- Sourcing policy: public JLC stock if available; otherwise JLCPCB Global Sourcing or consignment

Do not replace `U8`, the charger power stage, RP2040, battery polarity H-bridge, or battery contacts merely to reduce cost in Rev A.

## Charger power-stage identity

The golden Rev A audit deliberately distinguishes the two PMOS roles that are easy to misread during an EDA-library migration:

- `Q5 = SI2305CDS / C37577` is the **main 5 V switching PMOS**.
- `Q4 = DMG2301L / C102619` is the **DS2712 control/gate-enable PMOS**; it is not the main power switch.
- `U8.1 / CC1 -> Q4.G` enables the current-control path.
- `U8.6 / CSOUT -> Q4.D` provides the hysteretic current-control output.
- `Q4.S -> Q5.G` is the shared switch-gate control node.
- `R37 = 330 ohm / C105881` pulls that node to 5 V so Q5 defaults off.
- `R36 = 10 kOhm / C98220` biases Q4 gate-to-source.

The frozen buck path is:

`5V -> Q5 -> switch node -> L1 (47 uH) -> D4 -> Q2 polarity bridge -> R34 (0.124 ohm) -> GND`

Supporting components that must retain both orientation and placement are:

- `C30 = 10 uF / C15850`: local 5 V input decoupling
- `D2 = MBRA340 / C26178`: catch diode from GND to the Q5/L1 switch node
- `L1 = SRP7050TA-470M / C2047110`: 47 uH buck inductor
- `D4 = MBRA340 / C26178`: series diode into the H-bridge/VP1 high rail
- `D3 = MMBD4148 / C2928912`: output clamp to 5 V; package pin 2 is intentionally NC

`critical-power-stage-audit.json` also freezes the upstream XY/rotation/layer of `C30, D2, D3, D4, L1, Q4, Q5, R36, R37, U8`. This prevents a JLCEDA migration from preserving connectivity while accidentally spreading the high-di/dt loop or rotating a polarized device.

## Frozen mechanical references

`rev-a-baseline.json` records critical placements and the source board envelope. For the first board, the exact `Edge.Cuts` geometry and mechanical interface positions are treated as immutable unless an upstream error is proven.

Particularly sensitive references include:

- `J4/J5`: Keystone 590 battery contacts
- `J1`: USB-C
- `J3`: display connector
- `J2`: servo/header interface
- `U7`: VCNL4040 cell detector
- `TH1/TH2`: populated thermistors
- `TH3`: intentionally **DNP**; retain footprint/reference behavior but do not populate

## Repository verification

Run the complete static/golden-source guardrails:

```bash
python JLC/verify_rev_a.py
python JLC/verify_power_stage.py
python JLC/build_jlc_binding_manifest.py --check
python JLC/verify_jlc_export.py --self-test
node --check JLC/jlceda-scripts/import-rev-a-kicad.js
node --check JLC/jlceda-scripts/audit-jlc-library-bindings.js
node --check JLC/jlceda-scripts/export-rev-a-audit.js
```

`build_migration_bundle.py` reruns the electrical/mechanical reproduction guardrails before it is allowed to emit a JLCEDA migration ZIP.

The first full power-stage-enabled audit established:

- base reproduction audit: **492 checks passed, 0 failed**
- charger power-stage audit: **164 checks passed, 0 failed**
- total explicit electrical/mechanical equivalence checks: **656**

The CI run for commit `48495bade76f0b89e5447dce77934a83932506ef` additionally passed:

- generated JLC binding-manifest freshness check
- BOM/CPL round-trip verifier self-test
- syntax validation of all three standalone JLCEDA helpers
- deterministic migration bundle build
- migration artifact publication
- JLCEDA audit-kit artifact publication

That run publishes two artifacts:

1. `SPINC-JLC-Rev-A-KiCad-<sha>` — protected KiCad migration ZIP
2. `SPINC-JLC-Rev-A-Audit-Kit-<sha>` — binding manifest plus the three JLCEDA standalone audit/import helpers

The guardrails currently cover:

1. KiCad source identity and 4-layer construction
2. ENIG/black-mask source manufacturing intent
3. representative frozen `Edge.Cuts` primitives
4. complete LCSC binding in the production BOM
5. critical MPN/LCSC identities, including `DS2712E+ / C7455651`
6. all 16 DS2712 pin/function/net mappings and explicit channel-2 NC pins
7. DS2712 current-sense, CTST, TMR, VDD and THM1 programming networks
8. battery-polarity H-bridge topology and RP2040 control pins
9. charger control handoff `CC1/CSOUT -> Q4 -> Q5 gate`
10. Q5/D2/L1/D3/D4 switch-mode buck topology and diode orientation
11. buck-to-H-bridge high rail and H-bridge-to-R34/R35 sense-return handoff
12. critical charger power-stage XY/rotation/layer placement cluster
13. critical mechanical component XY/rotation/layer placements
14. no accidental bottom-side assembly components
15. preservation of `TH3` as DNP / excluded from BOM
16. pinned upstream golden commit and JLC stackup
17. generated 91-designator / 44-C-number JLC binding manifest
18. BOM/CPL round-trip comparison including C-number, side, rotation and relative-placement drift

## JLCEDA three-stage migration pipeline

**Important:** repository CI being green proves the source-side guardrails and helper syntax. It does **not** prove that a particular JLCEDA runtime has imported the board correctly. A migration only becomes accepted after the JLCEDA-side stages below produce and pass their evidence.

### Stage 1 — guarded KiCad import

Run in JLCEDA Pro V3 via `Advanced -> Run Script`:

`JLC/jlceda-scripts/import-rev-a-kicad.js`

Select only the CI-produced `SPINC-JLC-Rev-A-KiCad-<sha>.zip`.

The helper:

- refuses an unexpected ZIP name or implausibly small file
- imports the KiCad documents using source schematic style
- keeps symbol/footprint/3D associations during migration
- deliberately does **not** extract the KiCad libraries into the user's JLC library
- does **not** call the imported result production-ready

After import, preserve the original KiCad files unchanged as the upstream reference.

### Stage 2 — read-only JLC system-library audit

Before replacing or rebinding any component, run:

`JLC/jlceda-scripts/audit-jlc-library-bindings.js`

When prompted, select `JLC/jlc-bindings.json` from the matching Audit Kit/commit.

This helper is intentionally **read only**. It performs exact JLC system-library queries for all 44 frozen C-numbers and exports `SPINC-JLC-Rev-A-Library-Audit.json` containing:

- zero / one / multiple match classification for every C-number
- JLC device UUID and library UUID
- associated JLC symbol name/UUID
- associated JLC footprint name/UUID
- associated JLC 3D model when present
- original KiCad value/footprint and all affected designators
- the currently imported PCB footprint for each designator when a PCB document is available

Even a perfect 44/44 unique match **does not authorize automatic footprint replacement**. Package/pad/pin compatibility must be reviewed before mutation because the JLC library APIs are currently BETA and a matching C-number alone does not prove that replacing an imported footprint is geometrically harmless.

### Stage 3 — JLC production-evidence export

After library review/rebinding and PCB inspection, run:

`JLC/jlceda-scripts/export-rev-a-audit.js`

The helper runs PCB DRC and exports:

- `SPINC-JLC-Rev-A.epro2`
- `SPINC-JLC-Rev-A-BOM.csv`
- `SPINC-JLC-Rev-A-CPL.csv`
- JLCEDA netlist
- `SPINC-JLC-Rev-A-Gerber.zip`
- JLCEDA PCB information file

The BOM exporter explicitly includes `Supplier Part` under the stable column name `LCSC Part #`; it does not depend on the user's default BOM template.

Run the exported BOM/CPL through:

```bash
python JLC/verify_jlc_export.py \
  --bom SPINC-JLC-Rev-A-BOM.csv \
  --cpl SPINC-JLC-Rev-A-CPL.csv
```

The round-trip verifier permits a single harmless global coordinate-origin translation (and reports a detected coordinate-frame Y inversion), but rejects per-component placement drift, assembly-side changes, rotation changes, missing/extra BOM references, DNP leakage and C-number changes.

BOM/CPL cannot prove board-outline/cutout/copper-pour equivalence, which is why Stage 3 also exports Gerber and PCB information. Those must be inspected/diffed before ordering.

## Remaining JLCEDA acceptance work

A JLCEDA import is not accepted merely because it opens without an error dialog. Before first-board order:

1. run the three stages above in the actual target JLCEDA runtime
2. review every library-audit exception and footprint association
3. verify all nets pin-by-pin against the upstream schematic
4. verify exact board outline, internal cutouts, connectors, battery contacts, sensor locations and rotations
5. rebuild/inspect copper pours rather than assuming imported zones are identical
6. apply `JLC04161H-3313` and re-check USB routing/return path
7. keep Q5 as the main switching PMOS and Q4 as the DS2712 gate/control PMOS
8. inspect Q5/D2/L1/D4 high-di/dt routing, D3 orientation and R34/R35 Kelvin-quality current sensing manually
9. inspect H-bridge orientation and verify `BAT_A/BAT_B` remain the two bridge midpoints regardless of inserted-cell polarity
10. pass JLCEDA ERC/DRC and JLCPCB DFM review
11. pass BOM/CPL round-trip verification
12. diff Gerber board outline/internal cutout and copper-layer evidence against the golden source before ordering

## Golden-board gates

Rev A is only promoted to `golden` after all of the following pass on physical hardware:

- USB power and enumeration/programming
- RP2040 clock/flash/boot
- 3.3 V and auxiliary rails
- display interface
- VCNL4040 cell presence detection
- battery voltage ADC paths
- battery thermistor path
- polarity correction / H-bridge behavior
- DS2712 precharge and fast-charge behavior
- safe termination and error handling
- servo/mechanical loading interface
- exact mechanical fit with the upstream enclosure and battery path

Only after this golden board exists should Rev B introduce custom improvements.
