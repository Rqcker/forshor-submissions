"""The trust boundaries AGENTS.md states, checked on the workflow files themselves: PR code never meets credentials, the verifier is the base commit's,
and consumers of a PR job's artifact treat it as data. Text-level checks (no YAML dependency); they fail when a workflow edit weakens a boundary."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {p.name: p.read_text(encoding="utf-8") for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))}
ACTIONS = {p.parent.name: p.read_text(encoding="utf-8") for p in sorted((ROOT / ".github" / "actions").glob("*/action.yml"))}
CREDENTIALS = re.compile(r"id-token:\s*write|google-github-actions/|\$\{\{\s*secrets\.")
PR_TRIGGER = re.compile(r"^  pull_request(?:_target)?:", re.M)


def code(text):
    return re.sub(r"(?m)#.*$", "", text)


def steps(text):
    parts = re.split(r"(?m)^(?=\s*- (?:name|uses|id|run|if):)", text)
    return [p for p in parts[1:]]


class PullRequestCode(unittest.TestCase):
    def test_there_is_no_pull_request_target(self):
        for name, text in {**WORKFLOWS, **ACTIONS}.items():
            self.assertNotIn("pull_request_target", code(text), name)

    def test_workflows_run_by_pull_requests_hold_no_credentials_or_write_scopes(self):
        triggered = [n for n, t in WORKFLOWS.items() if PR_TRIGGER.search(t)]
        self.assertIn("table-generation-submission.yml", triggered)
        self.assertIn("contributor-cli.yml", triggered)
        for name in triggered:
            text = code(WORKFLOWS[name])
            self.assertIsNone(CREDENTIALS.search(text), f"{name} runs on pull_request and must not reach credentials")
            self.assertIsNone(re.search(r":\s*write\b", text), f"{name} runs on pull_request and must not request write permissions")

    def test_credential_bearing_workflows_are_never_started_by_a_pull_request(self):
        holders = [n for n, t in WORKFLOWS.items() if CREDENTIALS.search(code(t))]
        self.assertEqual(sorted(holders), ["daily-backup.yml", "publish-results.yml"])
        for name in holders:
            self.assertIsNone(PR_TRIGGER.search(WORKFLOWS[name]), name)
            self.assertNotRegex(code(WORKFLOWS[name]), r"github\.(head_ref|event\.pull_request)", f"{name} must not read pull-request-controlled values")

    def test_every_checkout_drops_the_token(self):
        for name, text in {**WORKFLOWS, **ACTIONS}.items():
            for step in steps(code(text)):
                if "actions/checkout@" in step:
                    self.assertRegex(step, r"persist-credentials:\s*false", f"{name}: a checkout keeps the token on disk:\n{step}")

    def test_the_verifier_is_the_base_commit_and_pr_code_is_only_compiled_by_it(self):
        text = code(WORKFLOWS["table-generation-submission.yml"])
        verifier = [s for s in steps(text) if "path: verifier" in s]
        self.assertEqual(len(verifier), 1)
        self.assertIn("github.event.pull_request.base.sha", verifier[0])
        self.assertNotIn("head", verifier[0])
        controls = [s for s in steps(text) if "path: controls" in s or "path: admission-controls" in s]
        self.assertEqual(len(controls), 2)
        for step in controls:
            self.assertRegex(step, r"ref:\s*main\b")
        for step in steps(text):
            if re.search(r"working-directory:\s*submission\b", step) and "run:" in step:
                for script in re.findall(r"(?:python3?|node|bash|sh)\s+(\S+)", step):
                    if "/" in script and not script.startswith("-"):
                        self.assertTrue(script.startswith(("../verifier/", "../controls/")), f"the submission tree must not supply the code it is checked with: {script}")
                self.assertNotRegex(step, r"(?m)^\s*(?:\./|python3? scripts/)", step)

    def test_trusted_consumers_run_main_code_and_treat_the_artifact_as_data(self):
        for name in ("publish-results.yml", "notify-submitters.yml"):
            text = code(WORKFLOWS[name])
            self.assertIn("workflow_run:", text)
            first = steps(text)[0]
            self.assertIn("actions/checkout@", first)
            self.assertRegex(first, r"ref:\s*main\b")
            self.assertNotRegex(text, r"(?:require\(|node |python3? |source |bash |sh )['\"]?\.?/?artifacts/", f"{name} must not execute downloaded artifacts")
            self.assertIn("path: artifacts", text)
            for step in steps(text):
                if "download-artifact@" in step:
                    self.assertIn("run-id:", step)
                    self.assertIn("repository: ${{ github.repository }}", step)

    def test_the_verifier_job_requests_no_write_scope_and_publishing_alone_may_write(self):
        text = code(WORKFLOWS["table-generation-submission.yml"])
        self.assertRegex(text, r"(?m)^permissions:\n  contents: read\n")
        for name, workflow in WORKFLOWS.items():
            if name not in ("publish-results.yml", "notify-submitters.yml"):
                self.assertIsNone(re.search(r"(?:contents|checks|pull-requests|actions):\s*write", code(workflow)), name)


if __name__ == "__main__":
    unittest.main()
