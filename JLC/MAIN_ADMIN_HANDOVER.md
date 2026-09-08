# Main handover: administrator-only steps

The source checks are not permission grants. Neither a PR body, a fixture test
in another repository, nor a locally downloaded ZIP satisfies the missing
SPINC required check or creates branch protection. **Do not merge, enable
auto-merge, rewrite main, or start live JLCEDA as part of these steps.**

## 1. Connect the existing strict-validation job

Use an administrator-controlled GitHub session. Create a fine-grained token
limited to `lcolok/jlc-eda-research`, with **Contents: read-only** and an expiry.
Do not use a broad administration token as the CI reader. Do not paste the
reader into chat, an issue, PR, shell history, or a tracked file.

Store it using the SPINC Actions repository secret UI, or the official CLI's
interactive input (the value is not a command-line argument):

```sh
gh secret set JLC_HARNESS_READ_TOKEN --repo lcolok/SPINC
```

The job consumes exactly that name. Secret presence is not proof of access:
a complete new `validate-pinned-flow` run must resolve the source pin, check
out the private repository at that full SHA, build it and validate this PR's
actual source. Rerun the latest source workflow identified in PR #1, not the
historical #45/#47 runs after the head changes. Run the whole workflow so the
aggregate gate is recomputed.

This is a public repository. Only trusted, reviewed code may run with access
to the private checkout. A Contents-only credential still grants access to
private source. Never use `pull_request_target`, copy the private repository
into public artifacts, or send secrets to a fork to make a check pass.
Environment-based approvals are a possible later hardening step but require
both an explicitly configured protected environment and a workflow change;
merely creating an environment or an environment-scoped secret is insufficient.

## 2. Protect main with actual GitHub settings

In branch protection for the exact branch `main`, require:

- A pull request (no direct main writes in this handover).
- `production-source-gate` from the **GitHub Actions** integration, with the
  branch up to date before merging. Never manually manufacture a green status.
- Conversation resolution, no force-push and no deletion; apply the rules to
  administrators and document any bypass policy.

A solo-maintained repository need not require an impossible second reviewer.
Keep a human review record. Do not blindly PUT a replacement policy over any
existing stronger rule/ruleset. Verify the actual settings by rereading them:

```sh
gh api repos/lcolok/SPINC/branches/main/protection
gh api 'repos/lcolok/SPINC/rulesets?includes_parents=true'
```

An authorization failure is **unknown**, not evidence that no rules exist.
An old `protected=false` snapshot must not be used in place of a fresh read.

## 3. Archive and retain version-specific evidence

Archive reviewed ZIP bytes plus their checksums and source/harness/run IDs in
the project's approved durable storage. Source evidence is not a manufacturing
release. Retain #45 as a historical source baseline, never as acceptance for a
new head. Validate downloaded digests before publication. A checksum receipt
without the matching bytes, a chat download, and 90-day Actions retention are
not permanent archive completion.

Review companion `lcolok/jlc-eda-research#2`. Do not delete `jlc-rev-a` while
main's external workflows still consume it. Keep both PRs Draft until these
steps and the latest CI/manual review are complete. A separate explicit
approval is required for any merge or live migration.

## Round-trip acceptance strengthened during handover

`verify_jlc_export.py` now rejects NaN/Inf, missing angles, ambiguous C-numbers,
incorrect per-board quantities and malformed or ambiguous CSV input. Explicit
mil values are converted rather than stripped; conflicting unit labels fail.
Failure replaces a previous PASS at the report path atomically. Mirrored
coordinate-frame detection still produces a warning requiring Gerber/polarity
review; it is not manufacturing approval. The 43 additional tests use copies
of the frozen 91-populated-reference CSVs; no production input is modified.

Run offline acceptance tests:

```sh
python -m unittest discover -s JLC -p 'test_*.py' -v
python JLC/verify_jlc_export.py --self-test
```

The command above contains no live harness action. Source CI remains fail-closed
until the actual private-reader authorization is configured.
