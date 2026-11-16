"""The README, AGENTS.md and the docs describe this repository as it is: links resolve, named paths exist, nothing from the earlier route's README or the
maintainers' workstations leaks into the participant-facing files, and the rules that keep the live epoch intact are written down."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / n for n in ("README.md", "AGENTS.md", "PROVENANCE.md")] + sorted((ROOT / "docs").glob("*.md"))
PARTICIPANT_FACING = [ROOT / "README.md", ROOT / "AGENTS.md"]
FORBIDDEN = re.compile(r"ShorTableOptimization|sto_site|PLAN_v2|/Users/|/home/joshua|JMC_NOTES|VerifiedQC|ForShor's|the pinned public ForShor")
NOT_IN_THE_REPOSITORY = {"TableGeneration/Submission/summary.tex"}   # the optional file a participant adds
PATH_ROOTS = ("TableGeneration", "scripts", "evaluation", "docs", "leaderboard", "policies", ".github")


def read(path):
    return path.read_text(encoding="utf-8")


class Docs(unittest.TestCase):
    def test_every_relative_link_resolves(self):
        for doc in DOCS:
            for target in re.findall(r"\]\(([^)\s]+)\)", read(doc)):
                if re.match(r"(https?:|mailto:|#)", target):
                    continue
                self.assertTrue((doc.parent / target.split("#")[0]).exists(), f"{doc.relative_to(ROOT)} links to {target}")

    def test_paths_in_backticks_exist_in_the_participant_facing_files(self):
        for doc in PARTICIPANT_FACING:
            for path in re.findall(r"`((?:%s)/[A-Za-z0-9_./-]*[A-Za-z0-9_])/?`" % "|".join(PATH_ROOTS), read(doc)):
                if "<" in path or "NN" in path or path in NOT_IN_THE_REPOSITORY:
                    continue
                self.assertTrue((ROOT / path).exists(), f"{doc.name}: {path} exists")

    def test_the_participant_facing_files_name_no_workstation_no_old_route_and_no_library_repository(self):
        for doc in PARTICIPANT_FACING:
            m = FORBIDDEN.search(read(doc))
            self.assertIsNone(m, f"{doc.name} contains {m and m.group(0)!r}")

    def test_readme_is_short_and_has_what_a_participant_needs(self):
        text = read(ROOT / "README.md")
        self.assertLessEqual(len(text.splitlines()), 150)
        for needle in ("./forshor setup", "./forshor check", "./forshor submit", "TableGeneration/Submission/Policy.lean", "namespace PolicySubmission",
                       "namespace PTPPolicySubmission", "summary.tex", "docs/OPERATIONS.md", "BENCHMARK.md", "leaderboard/", "https://forshor.ai", "PROVENANCE.md"):
            self.assertIn(needle, text)
        self.assertNotIn("Interim fork", text)
        self.assertNotRegex(text, r"linear\.app|MIS-\d+|QP-\d+|forshor-site")

    def test_agents_writes_down_the_rules_that_keep_the_live_epoch_and_the_caches_intact(self):
        text = read(ROOT / "AGENTS.md")
        for needle in ("evaluation/engine", "new epoch", "canon.epoch_of(epoch.model_of())", "evaluation/export_v2/evidence-archive", "submission-results",
                       "TableGeneration/", "evaluation/policy-trees.json", "scripts/leaderboard.py", "docs/POLICY_CI.md", "Policy.lean", "summary.tex"):
            self.assertIn(needle, text)

    def test_operations_has_the_queue_and_recovery_text_that_left_the_readme(self):
        text = read(ROOT / "docs/OPERATIONS.md")
        for needle in ("## Queues and recovery", "### Recover a failed publication", "gh run rerun PUBLISH_RUN_ID", "four active verification jobs"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
