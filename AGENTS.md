# SPINC: owned development checkout

## Identity and authority

- Owned repository: `https://github.com/lcolok/SPINC.git`.
- Canonical MMM4 checkout is normally `~/github/SPINC`; always trust the path returned by Devspace/open-workspace over a hard-coded physical volume path.
- `SPINC-upstream` is the separate CoretechR reference checkout; do not change its remote or use it as the owned working directory.
- `main` is intended to become the owned production-source mainline. This is a policy, not a claim that the handover has merged. Inspect actual branch/HEAD, PR and checks on every task.
- Current operating guide: `JLC/LOCAL_DEVELOPMENT.md`.
- Approval boundaries: `JLC/PRODUCTION_READINESS.md` and `JLC/MAIN_ADMIN_HANDOVER.md`.

## Context rebuild before core-code changes

1. Register the user's intent, verify the absolute path, remote, HEAD and complete Git status. Preserve all existing changes.
2. Run `tlens version`, `tlens doctor backend`, and `skldr version`.
3. Search both raw sessions and knowledge archives for SPINC, the feature and exact commit/identifiers. Read useful chunks/ctx refs when present. A search miss is not proof that work never happened.
4. Use the current binary's help. In the version checked on 2026-09-08, `tlens timeline` is removed; use `tlens sessions --last 30d` instead. `tlens cat` is not supported.
5. Reconcile history with current code and executable tests. Never treat a PR body, old receipt, or handoff as current execution evidence.
6. If journal APIs return `recorded:false`, record that limitation in the handoff. Do not claim registration succeeded.

## Offline work and acceptance

```sh
python3 -B JLC/verify_local.py --checks-only
python3 -B JLC/verify_local.py
```

The first command checks the working tree. The second additionally builds two
commit-bound migration bundles and verifies byte equality, CRC and payload
hashes. Full mode intentionally refuses dirty tracked sources; do not stash,
reset, weaken the guard or commit without authorization to make it pass.
Evidence goes into a fresh `JLC/out/local-verification/` directory each time.
The report records untracked tooling and its hashes; this is not a new commit.
All tests matching `JLC/test_*.py` are discovered by both this runner and source CI.
The packager checks both index and worktree, compares payload bytes against the
actual Git blobs, rejects input/Git/alias output paths and atomically publishes
only completed archives. It preserves an earlier output on failure; an existing
ZIP is never proof that a new invocation succeeded. The local runner also hashes
worktree/index diffs so an unchanged M status cannot hide edits during a run.

Read live repository readiness with `python3 -B JLC/verify_repository_readiness.py`.
It only performs explicit GET requests through existing gh authentication: exit
0 means inspected automated gates pass, 1 means confirmed blockers, and 2 means
unknown/incomplete evidence. It never grants merge/manufacturing approval. A
secret name being present is not proof of authorization: the exact PR workflow
and all required jobs must also succeed for the same head/base/run attempt.
Do not replace this with an old green run, a companion repository's result or
a personally generated success status. Do not copy the machine's broad gh token
into the CI reader secret. Settings/credential changes need explicit approval.

For loader compatibility, use the exact immutable `JLC/harness-pin.json` in an
isolated clean harness worktree. Use the configured Go execution transport;
do not bypass it by invoking a hidden/native Go compiler. `flow validate` is
read-only. Never substitute an arbitrary installed `jlc` for the pinned loader.

## Hard boundaries

Do not change frozen PCB/schematic/BOM/CPL/firmware or pin revisions merely to
satisfy governance tests. Source checks are not manufacturing DRC/DFM or physical
Golden-board acceptance. Local loader compatibility is not the GitHub required
`production-source-gate` check and does not supply private-repository authorization.

No real `flow run`, imports, project replacement, live board edits, service
restarts, manufacturing orders, secret changes, commits, pushes, merges or
branch deletion without explicit task authorization. Import is non-idempotent.
Do not copy private harness code/credentials into this public repository.

The historical `_worktrees/jlc-spinc-20260831` has independent pre-existing
uncommitted assets. Do not clean, overwrite or fold them into this repository
without a separate evidence-based review.

## Handoff

Record real commands/results, changed files, initial/final Git state, source and
harness SHAs, evidence paths, failed approaches and unknowns in a local handoff.
Publish it with `skldr write -f`, save the returned ctx reference, and read it
back. A knowledge-base report is not durable storage of manufacturing artifacts.
