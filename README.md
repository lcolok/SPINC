# SPINC - DIY Automatic Battery Charger

![](images/SPINC_assembled.jpg)

## Overview

SPINC is an Open-Source NiMH charging station that can automatically load and eject AA battery cells. Simply drop batteries into the top of the device, regardless of their polarity. The internal mechanism picks up one cell at a time and fast-charges them sequentially. Up to 7 fully charged batteries can be conveniently stored inside the device for easy access. In addition, SPINC doubles as a desk clock, displaying the date and time on its high-resolution LCD.

<div align="center">
  <img src="images/GUI_demo.gif" width="50%">
</div>

### Features
* Fast-charges AA NiMH cells at up to 1A
* Automatic cell loading and ejection
* Electronic polarity-correction
* Voltage, temperature and charge time monitoring
* 240x400px monochrome LCD
* Date and time display using LVGL interface
* Compact and fully 3D-printed

## Reproduce SPINC

This fork is converging the upstream design into a maker-reproducible **Golden Rev A** before any redesign. The machine-checkable inventory is `JLC/reproduction-kit.json`; the end-to-end build, print, assembly, flashing and physical bring-up procedure is `JLC/REPLICATION_RUNBOOK.md`.

Offline source/inventory acceptance:

```sh
python3 -B JLC/verify_local.py --checks-only
python3 -B JLC/verify_reproduction_kit.py
```

Golden Rev A firmware is governed separately by the pinned AID remote PlatformIO contract:

```sh
python3 -B Platformio/golden_build.py
python3 -B Platformio/golden_build.py --repro-audit
```

The first command is the normal one-command Golden firmware build; the second uses a distinct verbose AID dedup key and rechecks the same UF2/BIN/ELF digests. It may still hit an identical historical done cache entry, so fresh-execution proof comes from the `cacheHit=false` job receipts recorded in `Platformio/firmware-build-contract.json`. See `Platformio/README.md` for details.

A passing source or firmware-build check is not a manufacturing or physical-Golden approval. See `JLC/README.md` for the JLC migration contract and separate manufacturing gates.