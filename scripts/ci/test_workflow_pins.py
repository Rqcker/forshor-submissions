"""Every `uses:` in the repository's workflows and composite actions names a full commit SHA, so a moved or hijacked tag cannot change what CI runs.

Local actions (`./...`) are exempt. A tag-pinned action that could not be resolved to a SHA from information already in the repository is listed in
UNRESOLVED below with the reason; the list is a to-do, not a policy: pin the action, delete its entry, and this test keeps it pinned.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / ".github" / "workflows").glob("*.yml")) + sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)", re.M)
FULL_SHA = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")

# action reference -> why it is still a tag. Resolve each from the Actions run log ("Download action repository 'x@tag' (SHA:...)") or the action's
# releases, pin it with the tag in a trailing comment, and delete the entry.
UNRESOLVED = {
    "actions/checkout@v7": "no run log or local pin names the SHA behind this tag",
    "actions/cache/save@v4": "no run log or local pin names the SHA behind this tag",
    "actions/cache/restore@v4": "no run log or local pin names the SHA behind this tag",
}


def references():
    for path in FILES:
        for match in USES.finditer(path.read_text(encoding="utf-8")):
            yield path, match.group("ref")


class WorkflowPins(unittest.TestCase):
    def test_files_were_found(self):
        self.assertGreaterEqual(len(FILES), 10)
        self.assertGreater(len(list(references())), 30)

    def test_every_third_party_action_is_pinned_by_full_commit_sha(self):
        loose = [f"{path.relative_to(ROOT)}: {ref}" for path, ref in references()
                 if not ref.startswith("./") and not FULL_SHA.fullmatch(ref) and ref not in UNRESOLVED]
        self.assertEqual(loose, [], "pin by 40-hex commit SHA, with the tag in a trailing comment")

    def test_the_unresolved_list_names_only_actions_still_in_use_and_still_unpinned(self):
        seen = {ref for _, ref in references()}
        for ref in UNRESOLVED:
            self.assertIn(ref, seen, f"{ref} is no longer used: delete it from UNRESOLVED")
            self.assertNotRegex(ref, r"@[0-9a-f]{40}$", f"{ref} is pinned: delete it from UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
