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

The original production BOM already contains LCSC part numbers for nearly all populated components. The previously-unbound charger controller is now explicitly bound as:

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

## Verification

Run both guardrails:

```bash
python JLC/verify_rev_a.py
python JLC/verify_power_stage.py
```

`build_migration_bundle.py` runs both again before it is allowed to emit a JLCEDA migration ZIP. The same two verifiers run automatically in GitHub Actions when Rev A PCB/JLC files change.

The first full power-stage-enabled CI run (`b33a3abe13881a8a463c85014bb780c9e22dbc1e`) passed:

- base reproduction audit: **492 checks passed, 0 failed**
- charger power-stage audit: **164 checks passed, 0 failed**
- total explicit equivalence checks: **656**
- deterministic migration ZIP: generated and published successfully

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

## JLCEDA migration procedure

Rev A migration is not considered complete merely because JLCEDA can open the design.

1. Import/migrate the upstream KiCad 8 schematic and PCB into JLCEDA Pro using the CI-generated `SPINC-JLC-Rev-A-KiCad` bundle.
2. Preserve the KiCad source files unchanged as the upstream reference.
3. Re-bind every populated symbol/footprint to the intended JLC/LCSC component identity.
4. Verify all nets pin-by-pin against the upstream schematic.
5. Verify exact board outline, internal cutouts, connectors, battery contacts, sensor locations and rotations.
6. Rebuild/inspect copper pours rather than assuming imported zones are identical.
7. Apply `JLC04161H-3313` as the manufacturing stackup and re-check USB routing/return path.
8. Treat the charger cluster as frozen: Q5 is the main switching PMOS, Q4 is the DS2712 gate/control PMOS; do not swap their conceptual roles while remapping library components.
9. Inspect Q5/D2/L1/D4 high-di/dt routing, D3 orientation and the R34/R35 Kelvin-quality current-sense return manually.
10. Inspect the H-bridge orientation and verify `BAT_A/BAT_B` remain the two bridge midpoints regardless of inserted-cell polarity.
11. Run JLCEDA ERC/DRC and JLCPCB DFM checks.
12. Export JLC production BOM/CPL and compare them to the frozen Rev A baseline before ordering.

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
