#!/usr/bin/env python3
"""Read SPINC's actual GitHub gates without changing settings, secrets or PRs.

Exit 0: inspected automated gates pass (not permission to merge).
Exit 1: confirmed blockers. Exit 2: incomplete/unknown evidence.
Uses the existing gh authentication; never reads or prints credential values.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from verify_harness_pin import BINARY_ASSET, validate_pin
from verify_local import ROOT, digest, save_report, source_identity

REPOSITORY = "lcolok/SPINC"
WORKFLOW = ".github/workflows/jlc-rev-a-verify.yml"
GATE = "production-source-gate"
JOBS = {"verify-reproduction-baseline", "validate-pinned-flow", GATE}


class ReadError(RuntimeError):
    def __init__(self, endpoint: str, status=None, message="read failed"):
        super().__init__(f"{endpoint}: {message}")
        self.status, self.message = status, message


def read_api(endpoint: str, *, paginate=False):
    command = ["gh", "api", "--method", "GET", f"repos/{REPOSITORY}/{endpoint}"]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    try:
        response = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadError(endpoint, message=type(exc).__name__) from exc
    try:
        value = json.loads(response.stdout)
    except ValueError as exc:
        # Do not copy authentication/transport stderr into an evidence artifact.
        raise ReadError(endpoint, message="no valid JSON response") from exc
    if response.returncode:
        status = str(value.get("status", "")) if isinstance(value, dict) else ""
        message = value.get("message") if isinstance(value, dict) else None
        safe = "Branch not protected" if status == "404" and message == "Branch not protected" else "API authorization or transport failed"
        raise ReadError(endpoint, status, safe)
    return value


def collect(pr_number: int) -> dict:
    snapshot = {"repository": REPOSITORY, "pr_number": pr_number, "errors": []}

    def observe(key, action):
        try:
            snapshot[key] = action()
        except (ReadError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            snapshot["errors"].append(f"{key}: {exc}")

    observe("local", lambda: source_identity(ROOT))
    observe("main", lambda: read_api("branches/main"))
    observe("pr", lambda: read_api(f"pulls/{pr_number}"))
    # The pinned harness binary must be published at the release the pin names;
    # keep only the fields the policy needs (asset name, state, server digest).
    def harness_binary():
        pin = validate_pin(json.loads((ROOT / "JLC/harness-pin.json").read_text(encoding="utf-8")))
        release = read_api(f"releases/tags/{pin['binary_tag']}")
        return {"tag": pin["binary_tag"], "expected_sha256": pin["binary_sha256"],
                "release_tag": release.get("tag_name"), "draft": release.get("draft"),
                "assets": [{key: asset.get(key) for key in ("name", "state", "digest")}
                           for asset in release.get("assets", [])]}

    observe("harness_binary", harness_binary)

    def protection():
        try:
            return read_api("branches/main/protection")
        except ReadError as exc:
            if exc.status == "404" and exc.message == "Branch not protected":
                return None
            raise

    observe("protection", protection)
    observe("rulesets", lambda: [
        {key: rule[key] for key in ("id", "name", "enforcement", "target")}
        for page in read_api("rulesets?includes_parents=true&per_page=100", paginate=True)
        for rule in page])
    if "pr" in snapshot:
        head = snapshot["pr"]["head"]["sha"]
        observe("runs", lambda: read_api(f"actions/workflows/jlc-rev-a-verify.yml/runs?event=pull_request&head_sha={head}&per_page=1")["workflow_runs"])
        if snapshot.get("runs"):
            run = snapshot["runs"][0]
            observe("jobs", lambda: [job for page in read_api(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", paginate=True) for job in page["jobs"]])
            gate_jobs = [job for job in snapshot.get("jobs", []) if job["name"] == GATE]
            if len(gate_jobs) == 1:
                observe("gate_app", lambda: read_api(f"check-runs/{gate_jobs[0]['id']}")["app"])
    # A moving PR/main or rerun must not combine evidence from two observations.
    if "pr" in snapshot:
        observe("pr_end", lambda: read_api(f"pulls/{pr_number}"))
    if "main" in snapshot:
        observe("main_end", lambda: read_api("branches/main"))
    if snapshot.get("runs"):
        observe("run_end", lambda: read_api(f"actions/runs/{snapshot['runs'][0]['id']}"))
    return snapshot


def evaluate(snapshot: dict) -> dict:
    blockers, unknown = [], list(snapshot.get("errors", []))

    def require(condition, reason):
        if not condition:
            blockers.append(reason)

    try:
        local, pr, main = snapshot["local"], snapshot["pr"], snapshot["main"]
        require(snapshot["repository"] == REPOSITORY, "wrong-repository")
        require(pr["state"] == "open", "pr-not-open")
        require(pr["draft"] is False, "maintainer-draft-not-released")
        require(pr["base"]["ref"] == "main", "wrong-base-branch")
        require(pr["head"]["repo"]["full_name"] == REPOSITORY, "untrusted-fork-head")
        require(local["commit"] == pr["head"]["sha"], "local-head-does-not-match-pr")
        require(not local["status"], "unpublished-local-changes")
        require(pr["base"]["sha"] == main["commit"]["sha"], "pr-base-is-stale")
        binary = snapshot["harness_binary"]
        require(binary["release_tag"] == binary["tag"] and binary["draft"] is False, "pinned-harness-binary-not-published")
        assets = [asset for asset in binary["assets"] if asset["name"] == BINARY_ASSET]
        require(len(assets) == 1 and assets[0]["state"] == "uploaded", "pinned-harness-binary-not-published")
        if len(assets) == 1 and assets[0]["digest"] is None:
            unknown.append("release asset digest not reported by GitHub")
        elif len(assets) == 1:
            require(assets[0]["digest"] == "sha256:" + binary["expected_sha256"], "pinned-harness-binary-digest-mismatch")
        if pr["mergeable"] is None:
            unknown.append("GitHub mergeability is still computing")
        else:
            require(pr["mergeable"] is True, "merge-conflicts")
        for side in ("head", "base"):
            require(snapshot["pr_end"][side]["sha"] == pr[side]["sha"], "pr-changed-during-read")
        require(snapshot["main_end"]["commit"]["sha"] == main["commit"]["sha"], "main-changed-during-read")
        require(snapshot["pr_end"]["draft"] == pr["draft"] and snapshot["pr_end"]["state"] == pr["state"], "pr-review-state-changed")

        if not snapshot["runs"]:
            blockers.append("no-workflow-run-for-current-pr-head")
        else:
            run, jobs = snapshot["runs"][0], snapshot["jobs"]
            require(run["head_sha"] == pr["head"]["sha"] and run["event"] == "pull_request" and run["path"] == WORKFLOW, "workflow-source-mismatch")
            require(any(item["number"] == pr["number"] and item["head"]["sha"] == pr["head"]["sha"] and item["base"]["sha"] == main["commit"]["sha"] for item in run["pull_requests"]), "workflow-pr-base-mismatch")
            require(run["status"] == "completed" and run["conclusion"] == "success", "latest-pr-workflow-not-successful")
            require(snapshot["run_end"]["run_attempt"] == run["run_attempt"] and snapshot["run_end"]["status"] == run["status"] and snapshot["run_end"]["conclusion"] == run["conclusion"], "workflow-rerun-during-read")
            for name in sorted(JOBS):
                matches = [job for job in jobs if job["name"] == name]
                require(len(matches) == 1 and matches[0]["run_id"] == run["id"] and matches[0]["run_attempt"] == run["run_attempt"] and matches[0]["status"] == "completed" and matches[0]["conclusion"] == "success", f"job-not-successful:{name}")

        protection = snapshot["protection"]
        if protection is None:
            if any(rule["enforcement"] == "active" for rule in snapshot["rulesets"]):
                unknown.append("active rulesets require policy review; not inferred to be absent or sufficient")
            else:
                blockers.append("main-has-no-enforced-protection")
        else:
            require(bool(protection.get("required_pull_request_reviews")), "pull-request-not-required")
            require(protection.get("enforce_admins", {}).get("enabled") is True, "administrators-can-bypass")
            require(protection.get("required_conversation_resolution", {}).get("enabled") is True, "conversation-resolution-not-required")
            for key in ("allow_force_pushes", "allow_deletions"):
                require(protection.get(key, {}).get("enabled") is False, f"unsafe-or-unknown:{key}")
            status = protection.get("required_status_checks") or {}
            require(status.get("strict") is True, "up-to-date-base-not-required")
            app = snapshot.get("gate_app") or {}
            require(app.get("slug") == "github-actions" and type(app.get("id")) is int, "gate-app-unverified")
            require(any(check.get("context") == GATE and check.get("app_id") == app.get("id") and type(check.get("app_id")) is int for check in status.get("checks", [])), "required-gate-not-bound-to-observed-github-actions-app")
    except (KeyError, TypeError, IndexError) as exc:
        unknown.append(f"missing or malformed evidence: {exc}")
    return {"result": "unknown" if unknown else "blocked" if blockers else "automated-gates-pass-human-approval-still-required",
            "exit_code": 2 if unknown else 1 if blockers else 0,
            "blockers": list(dict.fromkeys(blockers)), "unknown": unknown}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, default=1)
    args = parser.parse_args(argv)
    if args.pr <= 0:
        parser.error("--pr must be positive")
    root = ROOT / "JLC/out/repository-readiness"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(tempfile.mkdtemp(prefix=stamp + "-", dir=root))
    report = {"schema": 1, "result": "running", "started_at_utc": stamp,
              "qualification": "read-only-repository-gates-not-merge-or-manufacturing-approval",
              "not_evaluated": ["human-review", "durable-artifact-retention", "companion-consumer-merge", "manufacturing", "physical-golden"]}
    save_report(directory, report)
    try:
        snapshot = collect(args.pr)
        report["snapshot"] = snapshot
        report.update(evaluate(snapshot))
    except (Exception, KeyboardInterrupt) as exc:
        report.update(result="unknown", exit_code=2, unknown=[type(exc).__name__])
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_report(directory, report)
    summary = directory / "summary.json"
    (directory / "SHA256SUMS").write_text(f"{digest(summary.read_bytes())}  summary.json\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("result", "blockers", "unknown")}, indent=2))
    print(f"Report: {summary}")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
