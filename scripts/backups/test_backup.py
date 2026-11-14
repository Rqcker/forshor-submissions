import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("backup", Path(__file__).with_name("backup.py"))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def test_restore_rejects_corruption_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            entry = backup.write_file(source, "results/item.json", b"original")
            backup.make_archive(source, root / "good.tgz", [entry], {}, "data")
            with tarfile.open(root / "good.tgz") as original, tarfile.open(root / "bad.tgz", "w:gz") as bad:
                for member in original.getmembers():
                    body = original.extractfile(member).read()
                    if member.name != "manifest.json":
                        body = b"modified"
                    member.size = len(body)
                    bad.addfile(member, io.BytesIO(body))
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                backup.restore_archive(root / "bad.tgz", root / "restore")
            self.assertEqual(list((root / "restore").iterdir()), [])

    def test_restore_rejects_links_traversal_and_duplicate_files(self):
        for name, link in [("../escape", False), ("/absolute", False), ("link", True)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                with tarfile.open(root / "bad.tgz", "w:gz") as tar:
                    raw = backup.json_bytes({"schema": backup.SCHEMA, "files": []})
                    member = tarfile.TarInfo("manifest.json")
                    member.size = len(raw)
                    tar.addfile(member, io.BytesIO(raw))
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/etc/passwd"
                    tar.addfile(member, io.BytesIO())
                with self.assertRaisesRegex(ValueError, "Unsafe restore"):
                    backup.restore_archive(root / "bad.tgz", root / "restore")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with tarfile.open(root / "bad.tgz", "w:gz") as tar:
                for _ in range(2):
                    tar.addfile(tarfile.TarInfo("manifest.json"), io.BytesIO())
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                backup.restore_archive(root / "bad.tgz", root / "restore")

    def test_restore_does_not_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "existing").write_text("keep")
            with self.assertRaisesRegex(ValueError, "must be empty"):
                backup.restore_archive(root / "unused.tgz", root)
            self.assertEqual((root / "existing").read_text(), "keep")

    def test_data_capture_pins_pointer_before_listing(self):
        snapshot = "a" * 64
        epoch = "b" * 16
        publication = {"snapshotId": snapshot, "epoch": epoch}
        pointer = backup.json_bytes({"publication": publication})
        state = backup.json_bytes({"publication": publication})
        immutable = f"results/v2/e/{epoch}/s/{snapshot}/dashboard.json"
        bodies = {("results/v2/dashboard.json", "1"): pointer,
                  (immutable, "1"): pointer, (f"private-state/v2/{snapshot}.json", "1"): state}
        calls = []

        class FakeGoogle:
            def metadata(self, bucket, name):
                if name == "results/dashboard.json":
                    raise backup.urllib.error.HTTPError("unused", 404, "missing", {}, None)
                calls.append("pin")
                return {"name": name, "generation": "1", "size": str(len(pointer))}

            def listing(self, bucket, prefix):
                self.assert_pinned()
                for (name, gen), body in bodies.items():
                    if name.startswith(prefix):
                        # A concurrent publication changed the moving pointer.
                        yield {"name": name, "generation": "2" if name == "results/v2/dashboard.json" else gen,
                               "size": str(len(body))}

            def assert_pinned(self):
                if "pin" not in calls:
                    raise AssertionError("Listing occurred before freezing the pointer")

            def read(self, bucket, name, generation):
                return bodies[(name, generation)]

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            entries, details = backup.capture_data(FakeGoogle(), root)
            self.assertEqual(len(entries), 3)
            self.assertEqual(details["publications"]["results/v2/dashboard.json"], publication)
            backup.make_archive(root, root.parent / (root.name + ".tgz"), entries, details, "data")
            archive = root.parent / (root.name + ".tgz")
            try:
                restored = root / "restored"
                backup.restore_archive(archive, restored)
                self.assertEqual((restored / "results/v2/dashboard.json").read_bytes(), pointer)
                (restored / f"private-state/v2/{snapshot}.json").write_bytes(b'{}')
                with self.assertRaises((ValueError, KeyError)):
                    backup.check_publication(restored)
            finally:
                archive.unlink()

    def test_upload_is_create_only_and_checks_downloaded_bytes(self):
        google = backup.Google("not-a-real-token")
        calls = []
        def fake(url, token, **kwargs):
            calls.append((url, kwargs))
            return b'{"generation":"123"}' if kwargs.get("method") == "POST" else b"body"
        with patch.object(backup, "request", fake):
            result = google.create("example", "daily/site/run/archive.tgz", b"body", "application/gzip")
        self.assertIn("ifGenerationMatch=0", calls[0][0])
        self.assertIn("generation=123", calls[1][0])
        self.assertEqual(result["sha256"], backup.sha256(b"body"))
        with patch.object(google, "read", return_value=b"corrupt"), patch.object(backup, "request", return_value=b'{"generation":"123"}'):
            with self.assertRaisesRegex(ValueError, "differs"):
                google.create("example", "daily/site/run/archive.tgz", b"body", "application/gzip")

    def test_invalid_run_identity_cannot_select_backup_paths(self):
        with self.assertRaisesRegex(ValueError, "run identity"):
            backup.run("site", None, "../previous-backup")

    def test_git_bundle_restores_branch_tag_pr_head_and_default_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo = root / "repo"
            backup.git(["init", "-b", "main", repo])
            backup.git(["-C", repo, "config", "user.name", "Backup test"])
            backup.git(["-C", repo, "config", "user.email", "backup-test@example.invalid"])
            (repo / "Policy.lean").write_text("original submission")
            backup.git(["-C", repo, "add", "Policy.lean"])
            backup.git(["-C", repo, "commit", "-m", "initial"])
            backup.git(["-C", repo, "branch", "submission-results"])
            backup.git(["-C", repo, "tag", "published"])
            backup.git(["-C", repo, "update-ref", "refs/pull/1/head", "HEAD"])
            payload = root / "payload"
            payload.mkdir()
            bundle = payload / "repository.bundle"
            backup.git(["-C", repo, "bundle", "create", bundle, "--all"])
            refs = backup.git(["-C", repo, "for-each-ref", "--format=%(refname) %(objectname)"])
            entries = [backup.write_file(payload, "repository.bundle", bundle.read_bytes()),
                       backup.write_file(payload, "refs.txt", refs.encode()),
                       backup.write_file(payload, "HEAD.txt", b"refs/heads/main\n")]
            archive = root / "repo.tgz"
            backup.make_archive(payload, archive, entries, {}, "submissions")
            with patch.dict(backup.os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "init.defaultBranch", "GIT_CONFIG_VALUE_0": "different-default"}):
                backup.restore_archive(archive, root / "restored")
            restored = root / "restored/restored.git"
            self.assertEqual(backup.git(["-C", restored, "for-each-ref", "--format=%(refname) %(objectname)"]), refs)
            self.assertEqual(backup.git(["-C", restored, "show", "main:Policy.lean"]), "original submission")


if __name__ == "__main__":
    unittest.main()
