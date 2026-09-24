# Main handover: administrator-only steps

The source checks are not permission grants. Neither a PR body, a fixture test
in another repository, nor a locally downloaded ZIP satisfies the missing
SPINC required check or creates branch protection. **Do not merge, enable
auto-merge, rewrite main, or start live JLCEDA as part of these steps.**

## 1. Publish the pinned harness binary (no CI credential)

`validate-pinned-flow` needs no secret. It downloads the exact reproducible
`linux/amd64` build of the pinned harness commit from this repository's
release and fails closed unless its SHA-256 equals `binary.sha256` in
`JLC/harness-pin.json`. The harness **source** stays private; only the built
executable is public. Do not create `JLC_HARNESS_READ_TOKEN`; delete it if an
older procedure created one.

Once per harness pin (`<commit>` = full pin SHA, `<tag>` = `binary.releaseTag`):

1. In `lcolok/jlc-eda-research`, run the builder on the exact commit. It builds
   twice with independent caches, requires byte equality, smoke-tests the bytes
   against the public SPINC flow including a negative control, and records the
   recipe and checksum:

   ```sh
   gh workflow run jlc-harness-binary.yml --repo lcolok/jlc-eda-research \
     -f commit=<commit> -f go-version=<binary.recipe.go>
   gh run watch --repo lcolok/jlc-eda-research --exit-status <run-id>
   gh run download <run-id> --repo lcolok/jlc-eda-research \
     --name jlc-<commit>-linux-amd64 --dir /tmp/jlc-<commit>
   (cd /tmp/jlc-<commit> && sha256sum -c SHA256SUMS && cat jlc-harness-binary.json)
   ```

2. Confirm `jlc-harness-binary.json` names `<commit>` and the pinned recipe,
   then publish the bytes as a release of this repository:

   ```sh
   gh release create <tag> --repo lcolok/SPINC --target jlc-rev-a \
     --title "jlc harness <commit>" \
     --notes-file /tmp/jlc-<commit>/jlc-harness-binary.json \
     /tmp/jlc-<commit>/jlc-linux-amd64 /tmp/jlc-<commit>/jlc-harness-binary.json
   gh api repos/lcolok/SPINC/releases/tags/<tag> \
     -q '.assets[] | select(.name=="jlc-linux-amd64") | .digest'
   ```

   The reported digest must be `sha256:<the builder's sha256>`. Enabling
   *immutable releases* for this repository is recommended hardening; the hash
   anchor already makes a replaced asset fail closed.

3. Commit that SHA-256 into `binary.sha256`. Until then `verify_harness_pin.py`
   rejects the pin and the source gate stays red by design.

A new harness pin means a new builder run, a new release tag and a new
`binary.sha256`; never replace assets under an existing tag. Anyone with
harness read access can re-run the builder or the recorded recipe on the same
commit and must obtain the same bytes.

Fork PRs run this job too, since no secret is involved. Never use
`pull_request_target` or send secrets to a fork to make a check pass.

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
