from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from scripts.ci.verification_identity import identity


class VerificationIdentityTest(unittest.TestCase):
    def test_committed_policy_options_guard_controls_receipt_reuse(self):
        with TemporaryDirectory(prefix="receipt-identity-") as d:
            root = Path(d)

            def git(*args):
                subprocess.run([
                    "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Local Test",
                    "-c", "user.email=local-test@example.invalid", *args], cwd=root, check=True,
                    stdout=subprocess.DEVNULL)

            git("init", "-q")
            helper = root / "evaluation/starter/pack/harness/policy_options.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("# initial guard\n")
            git("add", "--", "evaluation")
            git("commit", "-q", "--no-gpg-sign", "-m", "Initial guard")
            before = identity(root, "a" * 40)
            helper.write_text("# updated guard\n")
            self.assertEqual(identity(root, "a" * 40), before)
            git("add", "--", str(helper.relative_to(root)))
            git("commit", "-q", "--no-gpg-sign", "-m", "Update only the imported guard")
            self.assertNotEqual(identity(root, "a" * 40)["fingerprint"], before["fingerprint"])
