# Production-main handover

## Status and boundaries

`main` is the intended owned production-source mainline, not an upstream mirror.
This change admits the frozen Rev A source and migration tooling for review. It
does **not** approve a JLCEDA manufacturing package or a physical Golden board.
The electrical/mechanical source baseline remains upstream
`af7b36e8ca5e99bfb3e99d8b02d9864117091de7`. Hardware geometry, firmware and the
frozen component population are not changed by the mainline handover.

Three independent approvals must remain separate:

1. **Source approval:** source guardrails, deterministic inputs, and validation
   of this exact flow using this exact pinned harness.
2. **Manufacturing approval:** real JLCEDA import, library/footprint review,
   ERC/DRC, stackup, actual BOM/CPL round-trip, Gerber comparison and JLCPCB DFM.
3. **Physical Golden approval:** measured electrical, charging, sensor, USB,
   firmware and mechanical acceptance on an identified physical board.

See `README.md` in this directory for the complete hardware acceptance scope.
A green source gate is never permission to order boards or claim `golden`.

## CI and access prerequisites

`JLC Rev A verification` runs for all PRs targeting main and all pushes to main
or jlc-rev-a, without workflow-level path filtering. The stable required check
is **`production-source-gate`**. It fails unless both underlying jobs succeed:

- `verify-reproduction-baseline`: existing source guards, binding manifest,
  round-trip self-test, script syntax, provenance tests, two-build byte equality.
- `validate-pinned-flow`: builds the exact private harness commit named in
  `harness-pin.json`, then runs `jlc flow validate`, without live JLCEDA actions.

The shared harness repository is private. Configure the SPINC Actions secret
**`JLC_HARNESS_READ_TOKEN`** with only read access to Contents in
`lcolok/jlc-eda-research`. The ordinary SPINC `GITHUB_TOKEN` is not a cross-repo
credential. Do not publish the token, store it in Git, copy private harness
source into public artifacts, or use `pull_request_target` to bypass missing
fork secrets. Missing authorization intentionally fails the strict source gate.

Actions are pinned to observed full commits with Node 24 support. Self-hosted
live runners must support Node 24 (runner >= 2.327.1); verify the actual host
before attempting live execution. Linux source CI does not test that Mac host.

## Repository settings required before merging

This document specifies policy; committing it does **not** enable protection.
A repository administrator must apply and verify rules for `main`:

- Require a pull request and the `production-source-gate` check from GitHub
  Actions; require the PR to be up to date with main.
- Block force pushes and branch deletion; resolve review conversations.
- Record an explicit maintainer review. Do not require an impossible second
  reviewer in a single-maintainer project; document any emergency bypass.
- Do not enable auto-merge for this handover. Prefer a merge commit so historical
  source commits and their evidence associations remain in the ancestor chain.

## External consumers

The companion `jlc-eda-research` change must remove implicit `jlc-rev-a` reads
from both the live workflow and its SPINC flow-contract test. Live execution
must require an explicitly approved full SPINC commit SHA, record that SHA,
resolve the harness pin from that source, and run only after manual dispatch.
No push or pull-request event may start the live Mac migration job.

Do not delete jlc-rev-a until all consumers have migrated. An old or cancelled
live run is not evidence of successful migration. Before any recovery, inspect
the prior flow state: import-external is non-idempotent and must not be replayed
merely because a later export failed. Resume from a reviewed failed stage with
`--from`; never silently create a replacement project.

## Evidence and retention

Historical run #45 (33358345121) tested
`dd7def6b68982b6eda497bcf6d3e2b6ff0a43004`. Its two artifact ZIPs were downloaded
and independently SHA-256/CRC checked on 2026-09-08. The inner KiCad manifest's
30 payload hashes were also checked. See `evidence/source-dd7def6-receipt.json`.
This receipt is not a replacement for keeping the ZIP bytes.

New source CI publishes the input bundle, audit kit, and source evidence for
90 days. Source evidence includes the tested commit/tree, PR head/base,
workflow run/attempt, harness commit and checksums. The input ZIP contains only
commit-bound deterministic metadata; it no longer mislabels main/PR checkouts
as jlc-rev-a. Tracked dirty sources and untracked migration inputs are rejected.

Actions retention is **not permanent archiving**. Before merge, place the
verified historical ZIPs and final-candidate evidence in a durable approved
release/archive. The old run's artifacts expire around 2026-09-30 04:48 UTC.
Every new PR head needs its own evidence; after merging, generate and archive
fresh evidence for the final main SHA. Never relabel #45 as validation of a
new commit or a synthetic PR merge as the final production-main commit.

## Final handover checklist

- [ ] Repository administrator configured cross-repository read access.
- [ ] Both underlying CI jobs and production-source-gate pass for final candidate.
- [ ] Actual PR mergeability, full change list and maintainer review checked.
- [ ] Main branch protection applied and verified, not merely documented.
- [ ] Companion consumer change reviewed and merged independently.
- [ ] Verified historical and candidate artifacts durably archived.
- [ ] No remaining reference treats jlc-rev-a as an implicit production source.
- [ ] After approved merge: main push CI passes for its exact final commit.
- [ ] Before ordering: all separate manufacturing gates approved.
- [ ] Before a Golden designation: physical-board evidence approved.
