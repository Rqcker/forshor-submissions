"""Trusted origin for a verification receipt this process did not produce itself.

`intake_v2.publish_verification` prices a receipt that another job wrote (`--publish-from`, the production route). The
standing rule is that hosted intake takes its inputs from the trusted GitHub run and the publisher, NEVER from a
receipt the submitter supplies (`request.rehearse_qualified_request`'s docstring), so a receipt authorised nothing by
itself and a qualified measured readout was refused outright on this route -- publishable only from a single trusted
run that had just verified it.

THE PROOF ALREADY EXISTS AND WAS DISCARDED. `scripts/ci/publication.js` binds the downloaded artifact to the exact
source run, attempt, head commit and pull request (`inspect`, and `archive`'s
`result.metadata` comparison), and `scripts/ci/policy_publication.js:96-98` then stamps

    verification.ciVerification = {...identity, runId, runAttempt, pullRequest}

into the very file handed to `--publish-from`, where `identity` is `scripts/ci/verification_identity.py`'s
`{schema, key, headSha, fingerprint}`. Nothing under `evaluation/` ever read that stamp.

READING IT IS NOT ENOUGH. A stamp is a claim ABOUT a receipt, not a property OF one: the fields are public, so a
submitter can copy a valid stamp onto a receipt of their own. Two things turn it into a proof, and neither is the
receipt's say-so:

  * the publisher RE-DERIVES the verification identity from ITS OWN checkout, so a head commit, fingerprint or key
    that the receipt merely asserts cannot pass; and
  * the publisher RE-FETCHES the artifact the stamp names -- the recorded run and attempt, under the artifact name
    `publication.js` assigns -- and requires the receipt's body to BE that artifact. The COMPARISON authorises.

This is `archive_policy.write_policy`'s principle applied one level up. That gate compares the verification's
`leanSha256` AND `leanSource` against the file at the pull request's head commit rather than believing either of them;
lane WQ lifted exactly that agreement check into the hosted path because the gates were otherwise ruling on text that
need not be the submission. Here the whole receipt is compared against the artifact of the recorded run and attempt.

A fetch that does not answer is not a verdict. `OriginUnavailable` is a resource stop the caller reports as
`Incomplete` at stage `publisher` -- already in `scripts/ci/notify_submitters.js`'s `LIMIT_STAGES`, so a submitter is
told "publishing did not finish", never that their policy was rejected.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

#: The fields `scripts/ci/verification_identity.py:identity` produces. Every one is re-derived by the publisher and
#: compared; the receipt supplies none of them.
IDENTITY_FIELDS = ("schema", "key", "headSha", "fingerprint")

#: Where the trusted half records the proof (`scripts/ci/policy_publication.js:98`).
STAMP = "ciVerification"

#: How `scripts/ci/publication.js:108` names the verifier job's artifact. The ATTEMPT is part of the name, so asking
#: for this exact name is what pins the attempt: GitHub's artifact list is per run, not per attempt.
ARTIFACT = "table-generation-submission-{run_id}-{run_attempt}"

#: The file inside that artifact which `--publish-from` is pointed at (`publish_policy.py:39`).
MEMBER = "verification.json"

#: A receipt carries the submitted Lean source (at most 1 MB), the mirror envelope and the compiled document. Eight
#: mebibytes is far above any real receipt and well below anything that could exhaust the publisher.
MAX_MEMBER_BYTES = 8 * 1024 * 1024

#: The compressed artifact zip the transport will read before `read_member` bounds the member inside it. The zip
#: holds the same receipt, so this is the member bound with room for the container; it stops a hostile or broken
#: response from exhausting the publisher before the member bound can apply.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

REPOSITORY = "zapatacomputing/forshor-submissions"


class OriginUnavailable(RuntimeError):
    """The trusted artifact could not be fetched (transport, timeout, expiry). NOT a verdict on the submission."""


class OriginMismatch(ValueError):
    """The receipt is not what the trusted origin holds. A verdict: the submission does not qualify on this route."""


def artifact_name(run_id: Any, run_attempt: Any) -> str:
    return ARTIFACT.format(run_id=run_id, run_attempt=run_attempt)


def _body(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """The receipt as the verifier job wrote it: everything except the stamp the trusted half added afterwards."""
    return {key: value for key, value in receipt.items() if key != STAMP}


def _differences(receipt_body: Dict[str, Any], fetched: Dict[str, Any]) -> Iterable[str]:
    for key in sorted(set(receipt_body) | set(fetched)):
        if key not in fetched:
            yield f"{key} (not in the artifact)"
        elif key not in receipt_body:
            yield f"{key} (only in the artifact)"
        elif receipt_body[key] != fetched[key]:
            yield key


def read_member(blob: bytes, member: str = MEMBER) -> bytes:
    """`member` out of an artifact zip, with its uncompressed size bounded before anything is read."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            info = archive.getinfo(member)
            if info.file_size > MAX_MEMBER_BYTES:
                raise OriginUnavailable(f"the trusted artifact's {member} is larger than {MAX_MEMBER_BYTES} bytes")
            with archive.open(info) as handle:
                return handle.read(MAX_MEMBER_BYTES + 1)[:MAX_MEMBER_BYTES]
    except (KeyError, zipfile.BadZipFile, OSError) as error:
        raise OriginUnavailable(f"the trusted artifact does not hold a readable {member}: {error}") from error


def github_fetcher(token: str, *, opener: Callable[[str, str], bytes], repository: str = REPOSITORY,
                   timeout: int = 30) -> Callable[[str, Any, Any], bytes]:
    """A fetcher over GitHub's artifact API. `opener(url, accept) -> bytes` is the transport, so every decision this
    makes is testable without a network: which artifact the name selects, that an expired one is not it, and that the
    zip yields `verification.json`.

    UNPROVEN HERE: the transport itself. Wiring a real opener into the publish job is gate B4's half.
    """
    def fetch(name: str, run_id: Any, run_attempt: Any) -> bytes:
        del run_attempt                                   # the attempt is in `name`; the listing is per run
        try:
            listing = json.loads(opener(
                f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
                "application/vnd.github+json"))
            artifacts = [a for a in listing["artifacts"] if a.get("name") == name and not a.get("expired")]
            if not artifacts:
                raise OriginUnavailable(f"the trusted run holds no unexpired artifact named {name}")
            if len(artifacts) != 1:
                raise OriginUnavailable(f"the trusted run holds {len(artifacts)} artifacts named {name}")
            blob = opener(f"https://api.github.com/repos/{repository}/actions/artifacts/{artifacts[0]['id']}/zip",
                          "application/vnd.github+json")
        except OriginUnavailable:
            raise
        except Exception as error:                        # noqa: BLE001  transport, JSON shape, rate limits
            raise OriginUnavailable(f"the trusted artifact could not be fetched: {type(error).__name__}: {error}") from error
        return read_member(blob)

    del timeout                                           # the opener owns its own timeout
    return fetch


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Drop the credential when a redirect leaves the API host.

    The artifact download answers 302 to third-party blob storage whose URL already carries its own signed
    token. `urllib`'s default handler copies every header onto the redirected request, which would send this
    job's `GH_TOKEN` to that host. Nothing needs it there, so it does not go there.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            return None                                   # never downgrade off TLS
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and (urllib.parse.urlsplit(newurl).hostname
                                != urllib.parse.urlsplit(req.full_url).hostname):
            new.remove_header("Authorization")
        return new


def https_opener(token: str, *, timeout: int = 30, max_bytes: int = MAX_ARTIFACT_BYTES) -> Callable[[str, str], bytes]:
    """The production transport for `github_fetcher`: an authenticated HTTPS GET, bounded and timed.

    Every failure is raised as `OriginUnavailable`, never as a refusal -- the publisher not reaching GitHub says
    nothing about the submission (`intake_v2` turns it into `Incomplete("publisher", ...)`).

    UNPROVEN AGAINST THE REAL API. The decisions around it are tested offline with a fake transport; that the live
    endpoints answer as expected is gate B4 and needs a hosted run.
    """
    def open_url(url: str, accept: str) -> bytes:
        if urllib.parse.urlsplit(url).scheme != "https":
            raise OriginUnavailable(f"refusing a non-HTTPS artifact URL: {url!r}")
        request = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "forshor-submissions-publisher",
        })
        opener = urllib.request.build_opener(_StripAuthOnRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:
                # Read one byte past the bound: a truncated read would otherwise look like a short artifact.
                blob = response.read(max_bytes + 1)
        except OriginUnavailable:
            raise
        except Exception as error:                        # noqa: BLE001  HTTP status, DNS, TLS, timeout, rate limit
            raise OriginUnavailable(f"the trusted artifact could not be fetched from {url!r}: "
                                    f"{type(error).__name__}: {error}") from error
        if len(blob) > max_bytes:
            raise OriginUnavailable(f"the trusted artifact at {url!r} is larger than {max_bytes} bytes")
        return blob

    return open_url


def production_origin(token: str, *, root: Optional[Any] = None, repository: str = REPOSITORY,
                      timeout: int = 30) -> "ReceiptOrigin":
    """The `ReceiptOrigin` the publish job uses: this checkout's identity function and a real HTTPS transport."""
    return ReceiptOrigin(identity=lambda head_sha: checkout_identity(head_sha, root),
                         fetch=github_fetcher(token, opener=https_opener(token, timeout=timeout),
                                              repository=repository, timeout=timeout))


def checkout_identity(head_sha: str, root: Optional[Any] = None) -> Dict[str, Any]:
    """The verification identity of THIS checkout at `head_sha`, re-derived by the very function the trusted stamp was
    made with (`scripts/ci/verification_identity.py:identity`).

    It must be that function and not a second spelling of it: the stamp records a fingerprint of the `INPUTS` tree, so
    a reimplementation that drifted by one path would make every genuine receipt fail to match, and the gate would be
    unfalsifiable rather than strict.
    """
    import sys
    repo = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    ci = str(repo / "scripts" / "ci")
    if ci not in sys.path:
        sys.path.insert(0, ci)
    from verification_identity import identity           # the trusted half's own definition
    return identity(repo, head_sha)


class ReceiptOrigin:
    """Where the publisher independently obtains what a receipt claims.

    `identity(head_sha) -> {schema, key, headSha, fingerprint}` re-derives the verification identity from the
    publisher's own checkout (`scripts/ci/verification_identity.py:identity`).
    `fetch(name, run_id, run_attempt) -> bytes` returns the trusted artifact's `verification.json`.
    Both are injected so the rule can be proved offline against fakes.
    """

    def __init__(self, *, identity: Callable[[str], Dict[str, Any]], fetch: Callable[[str, Any, Any], bytes]):
        self.identity, self.fetch = identity, fetch


def establish(receipt: Dict[str, Any], *, head_sha: Optional[str], run_id: Optional[str], pull: int,
              origin: ReceiptOrigin) -> Dict[str, Any]:
    """Prove that `receipt` IS the trusted verifier job's artifact for this run, attempt, head and pull request.

    Returns the proof to record. Raises `OriginMismatch` (a refusal, naming what disagreed) or `OriginUnavailable`
    (a resource stop, never a verdict). `head_sha`, `run_id` and `pull` are the TRUSTED workflow's own values, which
    `scripts/ci/publish_policy.py` takes from `evaluation-metadata.json`, not from the receipt.
    """
    if not head_sha or not run_id:
        raise OriginMismatch("establishing a receipt's trusted origin needs the run's own --head-sha and --run-id")
    stamp = receipt.get(STAMP)
    if not isinstance(stamp, dict):
        raise OriginMismatch("the receipt carries no trusted verification receipt (ciVerification): it did not come "
                             "from the trusted publication job")
    expected = origin.identity(head_sha)                   # re-derived HERE, from the publisher's own checkout
    for field in IDENTITY_FIELDS:
        if stamp.get(field) != expected.get(field):
            raise OriginMismatch(f"the receipt's verification {field} is not this checkout's "
                                 f"({stamp.get(field)!r} against {expected.get(field)!r})")
    if str(stamp.get("runId")) != str(run_id):
        raise OriginMismatch(f"the receipt names run {stamp.get('runId')!r}, not this publication's run {run_id!r}")
    attempt = stamp.get("runAttempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise OriginMismatch(f"the receipt names no positive run attempt ({attempt!r})")
    if stamp.get("pullRequest") != pull:
        raise OriginMismatch(f"the receipt names pull request {stamp.get('pullRequest')!r}, not {pull!r}")

    name = artifact_name(run_id, attempt)
    try:
        raw = origin.fetch(name, run_id, attempt)
    except OriginUnavailable:
        raise
    except Exception as error:                            # noqa: BLE001  a fetcher failure is never a verdict
        raise OriginUnavailable(f"the trusted artifact {name} could not be fetched: "
                                f"{type(error).__name__}: {error}") from error
    try:
        fetched = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise OriginUnavailable(f"the trusted artifact {name} is not readable JSON: {error}") from error
    if not isinstance(fetched, dict):
        raise OriginUnavailable(f"the trusted artifact {name} does not hold a verification object")

    body = _body(receipt)
    differing = list(_differences(body, fetched))
    if differing:
        raise OriginMismatch(f"the receipt is not the artifact {name} the trusted run produced; it disagrees on "
                             f"{', '.join(differing[:6])}")
    from .canon import sha256_hex
    return {"schema": stamp["schema"], "artifact": name, "runId": str(run_id), "runAttempt": attempt,
            "pullRequest": pull, "headSha": expected["headSha"], "fingerprint": expected["fingerprint"],
            "key": expected["key"], "receiptSha256": sha256_hex(fetched)}
