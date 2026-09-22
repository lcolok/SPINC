"""Synthetic GitHub receipts only. These tests never call the live API."""
from __future__ import annotations

import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import verify_repository_readiness as readiness


def valid_snapshot():
    head, base = "a" * 40, "b" * 40
    pr = {"number": 1, "state": "open", "draft": False, "mergeable": True,
          "head": {"sha": head, "repo": {"full_name": readiness.REPOSITORY}},
          "base": {"ref": "main", "sha": base}}
    main = {"commit": {"sha": base}, "protected": True}
    run = {"id": 42, "run_attempt": 1, "head_sha": head, "event": "pull_request",
           "path": readiness.WORKFLOW, "status": "completed", "conclusion": "success",
           "pull_requests": [{"number": 1, "head": {"sha": head}, "base": {"sha": base}}]}
    protection = {"required_pull_request_reviews": {"required_approving_review_count": 0},
                  "enforce_admins": {"enabled": True},
                  "required_conversation_resolution": {"enabled": True},
                  "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
                  "required_status_checks": {"strict": True, "checks": [{"context": readiness.GATE, "app_id": 123}]}}
    return {"repository": readiness.REPOSITORY, "local": {"commit": head, "status": ""},
            "pr": pr, "pr_end": copy.deepcopy(pr), "main": main, "main_end": copy.deepcopy(main),
            "reader_present": True, "protection": protection, "rulesets": [], "errors": [],
            "runs": [run], "run_end": copy.deepcopy(run),
            "jobs": [{"id": i, "name": name, "run_id": 42, "run_attempt": 1,
                      "status": "completed", "conclusion": "success"} for i, name in enumerate(sorted(readiness.JOBS))],
            "gate_app": {"slug": "github-actions", "id": 123}}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = valid_snapshot()

    def blocked(self, reason):
        report = readiness.evaluate(self.snapshot)
        self.assertEqual(report["exit_code"], 1, report)
        self.assertIn(reason, report["blockers"])

    def test_automated_pass_is_not_merge_approval(self):
        result = readiness.evaluate(self.snapshot)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["result"], "automated-gates-pass-human-approval-still-required")

    def test_missing_reader(self):
        self.snapshot["reader_present"] = False
        self.blocked("missing-private-harness-reader")

    def test_draft_stays_blocked(self):
        self.snapshot["pr"]["draft"] = self.snapshot["pr_end"]["draft"] = True
        self.blocked("maintainer-draft-not-released")

    def test_unpublished_changes(self):
        self.snapshot["local"]["status"] = " M JLC/tool.py"
        self.blocked("unpublished-local-changes")

    def test_wrong_local_commit(self):
        self.snapshot["local"]["commit"] = "c" * 40
        self.blocked("local-head-does-not-match-pr")

    def test_old_base_receipt(self):
        self.snapshot["runs"][0]["pull_requests"][0]["base"]["sha"] = "c" * 40
        self.blocked("workflow-pr-base-mismatch")

    def test_wrong_workflow(self):
        self.snapshot["runs"][0]["path"] = ".github/workflows/unrelated.yml"
        self.blocked("workflow-source-mismatch")

    def test_missing_current_run(self):
        self.snapshot["runs"] = []
        self.blocked("no-workflow-run-for-current-pr-head")

    def test_all_non_success_conclusions_fail(self):
        for state in ("failure", "skipped", "cancelled", "neutral", "timed_out", None):
            with self.subTest(state=state):
                snapshot = valid_snapshot()
                snapshot["jobs"][0]["conclusion"] = state
                self.assertEqual(readiness.evaluate(snapshot)["exit_code"], 1)

    def test_duplicate_or_missing_job_fails(self):
        for jobs in (self.snapshot["jobs"] * 2, self.snapshot["jobs"][1:]):
            snapshot = valid_snapshot()
            snapshot["jobs"] = jobs
            self.assertEqual(readiness.evaluate(snapshot)["exit_code"], 1)

    def test_previous_attempt_cannot_supply_success(self):
        self.snapshot["jobs"][0]["run_attempt"] = 0
        self.blocked("job-not-successful:" + self.snapshot["jobs"][0]["name"])

    def test_rerun_during_collection_fails(self):
        self.snapshot["run_end"]["run_attempt"] = 2
        self.blocked("workflow-rerun-during-read")

    def test_main_changed_during_collection_fails(self):
        self.snapshot["main_end"]["commit"]["sha"] = "c" * 40
        self.blocked("main-changed-during-read")

    def test_pr_changed_during_collection_fails(self):
        self.snapshot["pr_end"]["head"]["sha"] = "c" * 40
        self.blocked("pr-changed-during-read")

    def test_missing_protection_is_confirmed_blocker(self):
        self.snapshot["protection"] = None
        self.blocked("main-has-no-enforced-protection")

    def test_active_ruleset_without_legacy_protection_needs_review(self):
        self.snapshot["protection"] = None
        self.snapshot["rulesets"] = [{"enforcement": "active"}]
        result = readiness.evaluate(self.snapshot)
        self.assertEqual(result["exit_code"], 2)
        self.assertNotIn("main-has-no-enforced-protection", result["blockers"])

    def test_authorization_failure_is_unknown_not_absent(self):
        self.snapshot["errors"] = ["protection: HTTP 403"]
        del self.snapshot["protection"]
        result = readiness.evaluate(self.snapshot)
        self.assertEqual(result["exit_code"], 2)
        self.assertNotIn("main-has-no-enforced-protection", result["blockers"])

    def test_missing_evidence_is_unknown(self):
        self.assertEqual(readiness.evaluate({})["exit_code"], 2)

    def test_any_app_context_does_not_satisfy_required_check(self):
        self.snapshot["protection"]["required_status_checks"]["checks"][0]["app_id"] = -1
        self.blocked("required-gate-not-bound-to-observed-github-actions-app")

    def test_wrong_status_app_fails(self):
        self.snapshot["gate_app"]["slug"] = "someone-else"
        self.blocked("gate-app-unverified")

    def test_admin_bypass_fails(self):
        self.snapshot["protection"]["enforce_admins"]["enabled"] = False
        self.blocked("administrators-can-bypass")

    def test_fork_is_not_given_reader_access(self):
        self.snapshot["pr"]["head"]["repo"]["full_name"] = "someone/SPINC"
        self.blocked("untrusted-fork-head")


class TransportTests(unittest.TestCase):
    def test_only_explicit_get_and_no_credential_argument(self):
        response = subprocess.CompletedProcess([], 0, "[]", "")
        with patch.object(readiness.subprocess, "run", return_value=response) as run:
            self.assertEqual(readiness.read_api("rulesets", paginate=True), [])
        command = run.call_args.args[0]
        self.assertEqual(command, ["gh", "api", "--method", "GET", "repos/lcolok/SPINC/rulesets", "--paginate", "--slurp"])

    def test_generic_404_is_not_unprotected(self):
        response = subprocess.CompletedProcess([], 1, json.dumps({"status": "404", "message": "Not Found"}), "sensitive diagnostic")
        with patch.object(readiness.subprocess, "run", return_value=response):
            with self.assertRaises(readiness.ReadError) as error:
                readiness.read_api("branches/main/protection")
        self.assertNotEqual(error.exception.message, "Branch not protected")
        self.assertNotIn("sensitive", str(error.exception))

    def test_explicit_unprotected_404_is_distinguished(self):
        response = subprocess.CompletedProcess([], 1, json.dumps({"status": "404", "message": "Branch not protected"}), "")
        with patch.object(readiness.subprocess, "run", return_value=response):
            with self.assertRaises(readiness.ReadError) as error:
                readiness.read_api("branches/main/protection")
        self.assertEqual(error.exception.message, "Branch not protected")

    def test_transport_failure_does_not_leak_stderr(self):
        response = subprocess.CompletedProcess([], 1, "not JSON", "private auth material")
        with patch.object(readiness.subprocess, "run", return_value=response):
            with self.assertRaises(readiness.ReadError) as error:
                readiness.read_api("actions/secrets")
        self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
