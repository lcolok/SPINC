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

Run:

```bash
python JLC/verify_rev_a.py
```

The same verifier runs automatically in GitHub Actions when Rev A PCB/JLC files change.

It currently guards:

1. KiCad source identity and 4-layer construction
2. ENIG/black-mask source manufacturing intent
3. representative frozen `Edge.Cuts` primitives
4. complete LCSC binding in the production BOM
5. critical MPN/LCSC identities, including `DS2712E+ / C7455651`
6. critical component XY/rotation/layer placements
7. no accidental bottom-side assembly components
8. preservation of `TH3` as DNP / excluded from BOM
9. pinned upstream golden commit and JLC stackup

## JLCEDA migration procedure

Rev A migration is not considered complete merely because JLCEDA can open the design.

1. Import/migrate the upstream KiCad 8 schematic and PCB into JLCEDA Pro.
2. Preserve the KiCad source files unchanged as the upstream reference.
3. Re-bind every populated symbol/footprint to the intended JLC/LCSC component identity.
4. Verify all nets pin-by-pin against the upstream schematic.
5. Verify exact board outline, internal cutouts, connectors, battery contacts, sensor locations and rotations.
6. Rebuild/inspect copper pours rather than assuming imported zones are identical.
7. Apply `JLC04161H-3313` as the manufacturing stackup and re-check USB routing/return path.
8. Inspect the DS2712 switch-mode charge-current loop, current-sense routing, H-bridge and power distribution manually.
9. Run JLCEDA ERC/DRC and JLCPCB DFM checks.
10. Export JLC production BOM/CPL and compare them to the frozen Rev A baseline before ordering.

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
