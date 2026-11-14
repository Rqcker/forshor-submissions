#!/usr/bin/env python3
"""Independent ForShor backups. Standard library only; never writes to a source.

This file is kept identical in forshor-site and forshor-submissions. Each
repository's scheduled workflow can read only its own GitHub repository.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "quantum-pilot-services"
DESTINATION = "zapata-forshor-backups-769861884059"
SOURCE = "forshor-services-forshor-results"
REPOSITORIES = {"site": "zapatacomputing/forshor-site", "submissions": "zapatacomputing/forshor-submissions"}
SCHEMA = "forshor.independent-backup/v1"


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def request(url, token, *, method="GET", data=None, content_type="application/json"):
    """Retry transient reads/writes; callers make writes create-only and idempotent."""
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Content-Type": content_type,
            "Accept-Encoding": "identity", "User-Agent": "ForShor-independent-backup/1",
        })
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 3:
                raise
        except urllib.error.URLError:
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Request retries exhausted")


class Google:
    def __init__(self, token):
        if not token:
            raise ValueError("A short-lived BACKUP_ACCESS_TOKEN is required")
        self.token = token

    def metadata(self, bucket, name, generation=None):
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{urllib.parse.quote(name, safe='')}"
        if generation:
            url += "?generation=" + str(generation)
        return json.loads(request(url, self.token))

    def read(self, bucket, name, generation=None):
        query = {"alt": "media"}
        if generation:
            query["generation"] = str(generation)
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{urllib.parse.quote(name, safe='')}?{urllib.parse.urlencode(query)}"
        return request(url, self.token)

    def listing(self, bucket, prefix):
        token = None
        while True:
            query = {"prefix": prefix, "maxResults": "1000", "fields": "items(name,generation,size,md5Hash,crc32c),nextPageToken"}
            if token:
                query["pageToken"] = token
            page = json.loads(request(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?{urllib.parse.urlencode(query)}", self.token))
            yield from page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                break

    def create(self, bucket, name, content, content_type):
        query = urllib.parse.urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": "0"})
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?{query}"
        try:
            metadata = json.loads(request(url, self.token, method="POST", data=content, content_type=content_type))
        except urllib.error.HTTPError as error:
            if error.code != 412:
                raise
            error.close()
            # A transport retry can find its own completed upload. Never overwrite it.
            metadata = self.metadata(bucket, name)
        restored = self.read(bucket, name, metadata["generation"])
        if restored != content:
            raise ValueError("Backup read-back differs from original bytes")
        return {"name": name, "generation": metadata["generation"], "sha256": sha256(restored), "bytes": len(restored)}

    def outcome(self, component, success):
        values = {"backup_run_failed": 0 if success else 1}
        if success:
            values["backup_last_success_unixtime"] = int(time.time())
        payload = {"timeSeries": [{
            "metric": {"type": "custom.googleapis.com/forshor/" + metric, "labels": {"component": component}},
            "resource": {"type": "global", "labels": {"project_id": PROJECT}},
            "metricKind": "GAUGE", "valueType": "INT64",
            "points": [{"interval": {"endTime": now()}, "value": {"int64Value": str(value)}}],
        } for metric, value in values.items()]}
        request(f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries", self.token,
                method="POST", data=json_bytes(payload))


def write_file(root, name, content):
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts or str(parts) != name or "\\" in name:
        raise ValueError("Unsafe backup path")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)
    return {"path": name, "sha256": sha256(content), "bytes": len(content)}


def check_publication(root):
    """Check restored pointer/state bindings and referenced current dashboard files."""
    result = {}
    for pointer, state_prefix in [("results/dashboard.json", "private-state"),
                                  ("results/v2/dashboard.json", "private-state/v2")]:
        path = root / pointer
        if not path.exists():
            if pointer == "results/v2/dashboard.json":
                raise ValueError("The live v2 pointer is missing")
            continue
        dashboard = json.loads(path.read_bytes())
        publication = dashboard["publication"]
        snapshot = publication["snapshotId"]
        if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
            raise ValueError("Invalid restored snapshot identity")
        ledger = json.loads((root / state_prefix / (snapshot + ".json")).read_bytes())
        ledger_publication = ledger["publication"] if "/v2/" in pointer else ledger["bundle"]["dashboard"]["publication"]
        if ledger_publication["snapshotId"] != snapshot:
            raise ValueError("Restored pointer and private publisher state disagree")
        if "/v2/" in pointer:
            epoch = publication["epoch"]
            immutable = root / f"results/v2/e/{epoch}/s/{snapshot}/dashboard.json"
            if json.loads(immutable.read_bytes()) != dashboard:
                raise ValueError("Restored pointer does not match its immutable dashboard")
        result[pointer] = publication
    return result


def capture_data(google, root):
    # Freeze live pointers BEFORE listing. Their referenced immutable objects and
    # ledgers were already written when the pointer was published. Later unrelated
    # objects are harmless; the moving pointer is always downloaded at this generation.
    pinned = {}
    for name in ["results/dashboard.json", "results/v2/dashboard.json"]:
        try:
            pinned[name] = google.metadata(SOURCE, name)
        except urllib.error.HTTPError as error:
            if error.code != 404 or "/v2/" in name:
                raise
            error.close()
    objects = {}
    for prefix in ("results/", "private-state/"):
        for item in google.listing(SOURCE, prefix):
            if not item["name"].endswith("/"):
                objects[item["name"]] = item
    objects.update(pinned)
    print(json.dumps({"event": "backup_data_inventory", "objects": len(objects)}), flush=True)
    def download(pair):
        name, item = pair
        body = google.read(SOURCE, name, item["generation"])
        if len(body) != int(item["size"]):
            raise ValueError("Source object size changed")
        if item.get("md5Hash") and base64.b64encode(hashlib.md5(body).digest()).decode() != item["md5Hash"]:
            raise ValueError("Source object checksum mismatch")
        return {**write_file(root, name, body), "sourceGeneration": item["generation"],
                "sourceCrc32c": item.get("crc32c")}
    with ThreadPoolExecutor(max_workers=8) as workers:
        entries = list(workers.map(download, sorted(objects.items())))
    return entries, {"sourceBucket": SOURCE, "publications": check_publication(root)}


def git(args, *, env=None):
    result = subprocess.run(["git", *map(str, args)], env=env, capture_output=True, text=True)
    if result.returncode:
        # Remote output is not logged: it may contain contributor text or credentials.
        raise RuntimeError("Git operation failed: " + str(args[0]))
    return result.stdout


def restore_git(bundle, destination, refs, head):
    if not head.startswith("refs/heads/"):
        raise ValueError("Invalid default Git branch")
    git(["check-ref-format", head])
    git(["clone", "--mirror", bundle, destination])
    if git(["-C", destination, "for-each-ref", "--format=%(refname) %(objectname)"]) != refs:
        raise ValueError("Restored Git references differ")
    git(["-C", destination, "show-ref", "--verify", head])
    # Bundles preserve refs/objects, but some Git versions infer HEAD using the
    # restore machine's defaults. Restore the separately recorded symbolic HEAD.
    git(["-C", destination, "symbolic-ref", "HEAD", head])
    git(["-C", destination, "fsck", "--full"])
    if git(["-C", destination, "symbolic-ref", "HEAD"]).strip() != head:
        raise ValueError("Restored default Git branch differs")


def capture_repo(component, root, github_token):
    repository = REPOSITORIES[component]
    print(json.dumps({"event": "backup_repository_started", "repository": repository}), flush=True)
    if not github_token:
        raise ValueError("GH_TOKEN is required for the repository's read-only job token")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
           "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + github_token).encode()).decode()}
    with tempfile.TemporaryDirectory(prefix="forshor-git-") as temp:
        mirror = Path(temp) / "repository.git"
        git(["clone", "--mirror", f"https://github.com/{repository}.git", mirror], env=env)
        # Keep original PR heads as well as branches/tags, even for unmerged PRs.
        git(["-C", mirror, "fetch", "origin", "+refs/pull/*/head:refs/pull/*/head"], env=env)
        refs = git(["-C", mirror, "for-each-ref", "--format=%(refname) %(objectname)"])
        head = git(["-C", mirror, "symbolic-ref", "HEAD"]).strip()
        bundle = root / "repository.bundle"
        git(["-C", mirror, "bundle", "create", bundle, "--all"])
        git(["-C", mirror, "bundle", "verify", bundle])
        # Verify recoverability into an empty Git repository before upload.
        restored = Path(temp) / "restored.git"
        restore_git(bundle, restored, refs, head)
    entries = [write_file(root, "repository.bundle", bundle.read_bytes()),
               write_file(root, "refs.txt", refs.encode()), write_file(root, "HEAD.txt", (head + "\n").encode())]
    metadata = json.loads(request(f"https://api.github.com/repos/{repository}", github_token))
    pulls = []
    page = 1
    while True:
        batch = json.loads(request(f"https://api.github.com/repos/{repository}/pulls?state=all&per_page=100&page={page}", github_token))
        pulls.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    entries += [write_file(root, "repository.json", json_bytes(metadata)),
                write_file(root, "pull-requests.json", json_bytes(pulls))]
    return entries, {"repository": repository, "refs": len(refs.splitlines()), "pullRequests": len(pulls), "head": head}


def make_archive(root, archive, entries, details, component):
    manifest = {"schema": SCHEMA, "component": component, "capturedAt": now(), "details": details, "files": entries}
    (root / "manifest.json").write_bytes(json_bytes(manifest))
    with tarfile.open(archive, "w:gz") as tar:
        for name in ["manifest.json", *[entry["path"] for entry in entries]]:
            tar.add(root / name, arcname=name, recursive=False)
    return manifest


def restore_archive(archive, destination):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore destination must be empty")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Duplicate archive path or missing manifest")
        for member in members:
            parts = PurePosixPath(member.name)
            if not member.isfile() or parts.is_absolute() or ".." in parts.parts or str(parts) != member.name or "\\" in member.name:
                raise ValueError("Unsafe restore archive")
        manifest = json.load(tar.extractfile("manifest.json"))
        if manifest.get("schema") != SCHEMA:
            raise ValueError("Unknown backup schema")
        expected = {entry["path"]: entry for entry in manifest["files"]}
        if len(expected) != len(manifest["files"]) or set(names) != {"manifest.json", *expected}:
            raise ValueError("Backup manifest and archive files differ")
        # Check every member BEFORE writing anything to the restore destination.
        for name, entry in expected.items():
            body = tar.extractfile(name).read()
            if len(body) != entry["bytes"] or sha256(body) != entry["sha256"]:
                raise ValueError("Backup file checksum mismatch")
        for name in names:
            write_file(destination, name, tar.extractfile(name).read())
    if manifest["component"] == "data":
        check_publication(destination)
    elif manifest["component"] in REPOSITORIES:
        mirror = destination / "restored.git"
        restore_git(destination / "repository.bundle", mirror, (destination / "refs.txt").read_text(),
                    (destination / "HEAD.txt").read_text().strip())
    else:
        raise ValueError("Unknown backup component")
    return manifest


def run(component, google, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
        raise ValueError("Invalid backup run identity")
    with tempfile.TemporaryDirectory(prefix="forshor-backup-") as temp:
        folder = Path(temp)
        source = folder / "source"
        source.mkdir(mode=0o700)
        if component == "data":
            entries, details = capture_data(google, source)
        else:
            entries, details = capture_repo(component, source, os.environ.get("GH_TOKEN"))
        archive = folder / "archive.tar.gz"
        make_archive(source, archive, entries, details, component)
        restore_archive(archive, folder / "restore")
        prefix = f"daily/{component}/{now()[:10]}/{run_id}"
        stored = google.create(DESTINATION, prefix + "/archive.tar.gz", archive.read_bytes(), "application/gzip")
        complete = {"schema": SCHEMA, "component": component, "completedAt": now(), "archive": stored,
                    "details": details, "files": len(entries), "uncompressedBytes": sum(entry["bytes"] for entry in entries),
                    "runUrl": os.environ.get("BACKUP_RUN_URL")}
        google.create(DESTINATION, prefix + "/complete.json", json_bytes(complete), "application/json")
        google.outcome(component, True)
        print(json.dumps({"event": "backup_verified", "component": component, "backup": "gs://" + DESTINATION + "/" + prefix,
                          "files": len(entries), "archiveBytes": stored["bytes"], "sha256": stored["sha256"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("run")
    save.add_argument("component", choices=[*REPOSITORIES, "data"])
    save.add_argument("--run-id", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "restore":
        manifest = restore_archive(args.archive, args.destination)
        print(json.dumps({"restored": manifest["component"], "files": len(manifest["files"]), "destination": str(args.destination)}))
        return
    google = Google(os.environ.get("BACKUP_ACCESS_TOKEN"))
    try:
        run(args.component, google, args.run_id)
    except Exception as error:
        try:
            google.outcome(args.component, False)
        except Exception:
            pass  # An authentication failure is also covered by the missing-backup alert.
        # Do not echo exception content: remote errors or Git output can carry secrets.
        print(json.dumps({"event": "backup_failed", "component": args.component, "errorType": type(error).__name__,
                          "httpStatus": error.code if isinstance(error, urllib.error.HTTPError) else None}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
