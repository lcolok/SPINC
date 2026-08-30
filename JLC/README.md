# SPINC JLC Rev A

This directory governs the first JLC-ecosystem reproduction of the upstream SPINC AA NiMH charger.

## Goal

Produce a **Golden Rev A** that is electrically and mechanically equivalent to upstream SPINC before any custom feature, chemistry expansion, sourcing optimization, cost reduction, topology change, or mechanical redesign.

Pinned upstream source baseline:

- Repository: `CoretechR/SPINC`
- Commit: `af7b36e8ca5e99bfb3e99d8b02d9864117091de7`
- Source EDA: KiCad 8
- Target EDA after accepted migration: **JLCEDA Pro**

The runtime executor is the Go-based Flow Harness in `lcolok/jlc-eda-research`. SPINC itself only carries a declarative board identity and flow spec:

- `JLC/rev-a/board-id`
- `JLC/rev-a/flows/migrate.yaml`

There is deliberately **no shell orchestration SSoT**. The old `run_rev_a_harness.sh` bootstrap prototype has been removed.

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

The selected JLC stackup is intentionally close to the upstream board construction. Rev A does not use migration as an opportunity to optimize the stackup.

## Frozen component identity

The upstream production BOM remains the source of truth. `build_jlc_binding_manifest.py` regenerates `jlc-bindings.json`, and CI rejects a stale binding manifest.

Current frozen population:

- **91 populated designators**
- **44 unique LCSC/JLC C-numbers**
- `TH3` is explicitly **DNP**
- `U8 = DS2712E+ / C7455651 / TSSOP-16`

For Golden Rev A, do not replace DS2712, RP2040, the charger power stage, polarity bridge, battery contacts, or other upstream identities merely for sourcing or cost improvements.

## Charger topology that must not be reinterpreted

- `Q5 = SI2305CDS / C37577` is the **main 5 V switching PMOS**.
- `Q4 = DMG2301L / C102619` is the **DS2712 gate/control PMOS**, not the main power switch.
- `U8.1 / CC1 -> Q4.G`
- `U8.6 / CSOUT -> Q4.D`
- `Q4.S -> Q5.G`
- `R37 = 330 ohm / C105881` pulls the Q5 gate-control node to 5 V so Q5 defaults off.
- `R36 = 10 kOhm / C98220` biases Q4 gate-to-source.

Frozen charge path:

`5V -> Q5 -> switch node -> L1 (47 uH) -> D4 -> Q2 polarity bridge -> battery -> Q2 low rail -> R34 (0.124 ohm) -> GND`

Supporting frozen parts include `C30`, `D2`, `L1`, `D4`, and `D3`. `critical-power-stage-audit.json` also freezes XY/rotation/layer for the safety-critical switching cluster.

## Frozen mechanical references

`rev-a-baseline.json` records critical placement and board geometry. Until an upstream error is proven, the board outline, internal cutouts and mechanical interfaces are immutable.

Particularly sensitive references include `J4/J5` battery contacts, `J1` USB-C, `J3` display connector, `J2` servo/header interface, `U7` VCNL4040, `TH1/TH2`, and DNP `TH3`.

## Source-side verification

The Golden source is already protected by:

- base reproduction audit: **492 checks passed, 0 failed**
- charger power-stage audit: **164 checks passed, 0 failed**
- total explicit electrical/mechanical equivalence checks: **656**
- generated 91-designator / 44-C-number binding manifest
- deterministic KiCad migration bundle
- BOM/CPL round-trip self-test
- fallback JLCEDA script syntax checks

Repository CI proves the frozen source and tooling. It does **not** prove a live JLCEDA migration or a physical board.

## Primary runtime path — Go Flow Harness

The formal entry point is:

```bash
jlc flow validate --board JLC/rev-a --flow migrate
jlc flow run --board JLC/rev-a --flow migrate
```

`flow validate` runs the same strict Go loader/semantic validator used by `flow run`, without opening the state DB or executing any action.

The authored flow is `JLC/rev-a/flows/migrate.yaml`. It contains fine-grained fail-closed stages for:

1. Golden source and charger-power-stage preflight
2. generated JLC binding-manifest preflight
3. required native CPL capability preflight
4. deterministic KiCad migration-bundle creation
5. JLCEDA Web-first runtime bootstrap
6. fail-closed KiCad external import
7. PCB activation
8. JLCEDA PCB DRC
9. JLC BOM export with identity-drift verification
10. native JLCEDA CPL export
11. Gerber export
12. `.epro2` project export
13. BOM/CPL round-trip against the frozen KiCad production data

The Go flow engine records stage events, stdout/stderr/exit code evidence, gate verdicts, and SHA-256 for declared output files in its state store. SPINC therefore does not maintain its own phase/log/evidence orchestration code.

### Safe recovery after a later-stage failure

`import-external` creates a new JLCEDA project and is intentionally non-idempotent. Do not replay it just because a later DRC/export gate failed.

The shared Go harness supports an explicit restart point:

```bash
jlc flow status --board JLC/rev-a --flow migrate
jlc flow run --board JLC/rev-a --flow migrate --from <failed-stage-id>
```

All `preflight` checks still run again, but execution begins at the explicitly named machine stage. This prevents accidental duplicate project creation while retaining fresh Golden-source verification.

## Round-trip policy

`verify_jlc_export.py` accepts a harmless global coordinate-origin translation and can recognize a Y-axis coordinate-frame inversion. It rejects:

- per-component placement drift
- assembly-side changes
- rotation changes
- missing or unexpected assembled references
- `TH3` DNP leakage
- C-number changes

It accepts the JLC ecosystem's normal `LCSC Part #` / `JLCPCB Part #` naming and the shared harness's native `Supplier Part` BOM column. No manual CSV editing is part of the production flow.

## Manual fallback only

If the Go CLI/Bridge cannot be used on the target machine, these standalone JLCEDA Pro scripts remain for diagnosis/recovery only:

- `JLC/jlceda-scripts/import-rev-a-kicad.js`
- `JLC/jlceda-scripts/audit-jlc-library-bindings.js`
- `JLC/jlceda-scripts/export-rev-a-audit.js`

New production behavior belongs in the shared Go harness, not in a permanent SPINC-specific shell/JS orchestration track.

## Current runtime boundary

**The live SPINC JLCEDA migration has not yet been declared successful.** The source gates and shared Go harness tests are green, but the flow still has to be executed against the actual logged-in target JLCEDA runtime.

Before first-board order we still require:

1. execute the Go flow against the real JLCEDA session
2. capture actual importer `projectDataTypes`
3. review real library/footprint associations
4. verify board outline and internal cutouts
5. inspect imported copper pours
6. apply `JLC04161H-3313` and re-check USB routing/return path
7. manually inspect Q5/D2/L1/D4, D3 orientation and R34/R35 sensing
8. verify the H-bridge and `BAT_A/BAT_B` midpoints
9. pass JLCEDA ERC/DRC and JLCPCB DFM review
10. pass BOM/CPL round-trip
11. diff Gerber outline/cutout/copper evidence against the Golden source

## Physical Golden-board gates

Rev A is promoted to `golden` only after physical hardware passes USB/programming, RP2040 clock/flash/boot, power rails, display, VCNL4040, battery voltage and thermistor sensing, polarity correction, DS2712 precharge/fast-charge/termination, servo/mechanics, and exact enclosure/battery-path fit.

Only after this Golden board exists should Rev B introduce custom improvements.
