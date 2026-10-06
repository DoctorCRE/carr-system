#!/usr/bin/env python3
"""Publish and observe the evidence contract for the cloud backup workflow.

The producer writes one ``Backup artifact`` Check bound to the repository,
workflow run, attempt and commit. A successful Check is possible only after the
artifact API readback matches the producer's ID and metadata. The observer is
read-only: it classifies terminal provider state for a separate caller and
never sends a notification itself.

``validate-controlled-failure`` and ``controlled-failure`` are the WR54 seam
that lets one manually dispatched run end in a real, exactly attributed failure
without touching a credential or a dump — the only honest way to find out
whether GitHub's failure email actually reaches a human. The first is read-only
and refuses before anything durable exists; the second repeats every guard at
the write boundary, flips this run's own in-progress Check to a structured
backup-failure, and exits ``CONTROLLED_FAILURE_EXIT`` so the workflow itself
concludes failure.

WHAT THE HELPER CAN AND CANNOT ENFORCE. It can prove runtime provenance: the
event, both actors, the repository, the ref, that the supplied head equals this
run's own GITHUB_SHA, that the proof ID is a canonical UUID unused on this
head, that the run holds zero artifacts, and that exactly one exact-bound
in-progress Check exists. It CANNOT prove the supplied UUID is the one a human
approved — there is no durable allowlist here, and adding a generic approval
oracle was explicitly out of scope. A dispatch by the same GitHub account with
a different fresh UUID satisfies every check below. Binding a dispatch to an
approved effect packet is the supervised operator's job, done by reading the
run's own recorded inputs back afterwards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# IMPORTED UNDER PRIVATE NAMES ON PURPOSE. A plain `from lib.platform_metering
# import authorize_metered_execution` binds that callable as a PUBLIC attribute
# of this module, so anyone who imports this file re-reaches the metering gate
# through it and gets the privileged `admitted` outcome back from their own
# policy and request objects. The standing authority rule forbids exporting that
# outcome under ANY name, and a re-export is a name. The underscore keeps both
# out of this module's public surface; _public_surface_contract in
# ops/backup-workflow-selftest.py holds the property and fails if either is
# rebound publicly again.
from lib.platform_metering import (  # noqa: E402
    MeteringRefusal as _MeteringRefusal,
    authorize_metered_execution as _authorize_metered_execution,
)


CHECK_NAME = "Backup artifact"
REQUIRED_STEPS = ["dump", "encrypt", "upload", "readback"]
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ARTIFACT_NAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+-run-([1-9][0-9]*)-attempt-([1-9][0-9]*)(?:\.sql\.age)?$"
)
CHECKS_PER_PAGE = 100
MAX_CHECK_PAGES = 5

# ── the WR54 controlled-failure seam ────────────────────────────────────────
# Every one of these is a literal because the seam is deliberately narrow: it
# exists for one repository, on one branch, dispatched by one account. A guard
# that read its own expected value from the environment would be no guard.
CONTROLLED_FAILURE_EVENT = "workflow_dispatch"
CONTROLLED_FAILURE_ACTOR = "jbookout"
CONTROLLED_FAILURE_REPOSITORY = "jbookout/carr-system"
CONTROLLED_FAILURE_REF = "refs/heads/main"
CONTROLLED_FAILURE_REASON = "approved WR54 controlled failure before dump"
# The one workflow the reviewed dispatch door below can start. A literal for the
# same reason the four above are literals: a dispatcher that took its target as
# an argument would be a general door wearing this seam's name.
BACKUP_WORKFLOW_FILE = "backup-nightly.yml"
# A DOCUMENTED, DISTINCT NONZERO EXIT. 2 already means "refused, nothing
# written", so reusing it here would make a successful proof indistinguishable
# from a rejected one in the step log. 9 means the opposite: the Check WAS
# written and this process failed the run on purpose.
CONTROLLED_FAILURE_EXIT = 9
# ── whose attribution the head's Check evidence is decided on ───────────────
# EVERY INPUT TO BOTH DECISIONS BELOW -- which row is the seam's own and may be
# set aside, and which row is the repository's required check and may be stood
# on -- IS THE PROVIDER'S OWN ATTRIBUTION, and nothing the Check's creator
# wrote. An earlier revision paired the app stamp below with
# the seam's external_id envelope, and review forged it: the stamp is shared by
# every Actions Check on the head, ``external_id`` is a string the creator
# supplies, so a valid envelope under the shared stamp excluded a red Check that
# the seam never wrote. Name, title, output and external_id are all free text an
# installation can copy byte for byte, and none of them is consulted now.
# The app stamp IS the provider's attribution of which installation created a
# row -- necessary, and on its own nowhere near sufficient. Re-verify the pair:
#   gh api /repos/jbookout/carr-system/commits/<sha>/check-runs \
#     --jq '.check_runs[] | select(.name=="Backup artifact") | .app | {id, slug}'
# IF EITHER LITERAL IS WRONG THE EXCLUSION SIMPLY NEVER FIRES: the seam's own
# earlier failure then counts as red and a later proof refuses. That is the
# fail-closed direction on purpose -- a stale producer id costs a refusal, it
# never admits a spend.
ACTIONS_APP_ID = 15368
ACTIONS_APP_SLUG = "github-actions"
# ── the Check the head's POSITIVE evidence has to actually be ───────────────
# A green head is not "no red rows". AGENTS.md states the repository's own
# requirement -- ruleset "main: CI must be green" requires the `ops/ci.sh
# --strict` status check -- and ops/config/automerge-pilot-policy.v1.json
# registers that check's provider identity: the name below, the Actions app
# slug above, and the workflow file that produces it. Those three are restated
# here as literals rather than read from the policy file at runtime, because
# this door resolves its own tree (see _paused_policy_tree in the selftest) and
# a second file to load would be a second thing that can go missing between the
# guards and the POST. The selftest holds the two spellings equal instead.
#
# THE NAME IS NOT WHAT AUTHENTICATES IT. `ops/ci.sh --strict` is free text any
# creator can attach, exactly like "Backup artifact" was. What authenticates is
# the same provider fact the exclusion stands on: GitHub assigns a check-run's
# check-suite, and the suites of ci.yml's runs on THIS head are resolved from
# the provider's own run listing first. The name only SELECTS among rows that
# are already provider-authenticated as this workflow's output on this commit.
REQUIRED_CHECK_NAME = "ops/ci.sh --strict"
REQUIRED_CHECK_WORKFLOW_FILE = "ci.yml"
REQUIRED_CHECK_WORKFLOW_PATH = ".github/workflows/" + REQUIRED_CHECK_WORKFLOW_FILE
# STRICTER THAN NON_FAILING_CHECK_CONCLUSIONS ON PURPOSE. A neutral or skipped
# required check means the suite did not run, which is the absence of evidence,
# not evidence of green. Only "success" carries the admission.
REQUIRED_CHECK_CONCLUSION = "success"
# The repository path the provider reports on a workflow run, as opposed to the
# bare file name the dispatch endpoint takes. ``workflow_run.path`` is GitHub's
# own statement of WHICH WORKFLOW FILE produced a run, and it is the fact the
# exclusion is bound to.
BACKUP_WORKFLOW_PATH = ".github/workflows/" + BACKUP_WORKFLOW_FILE
# The run listing is paged exactly like the Check listing above, and fails
# closed on an exhausted cap for the same reason.
RUNS_PER_PAGE = 100
MAX_RUN_PAGES = 5
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class StatusError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    repository: str
    run_id: int
    run_attempt: int
    head_sha: str

    @classmethod
    def environment(cls) -> "Identity":
        repository = required_env("GITHUB_REPOSITORY")
        run_id = positive_int(required_env("GITHUB_RUN_ID"), "GITHUB_RUN_ID")
        attempt = positive_int(required_env("GITHUB_RUN_ATTEMPT"), "GITHUB_RUN_ATTEMPT")
        head = required_env("GITHUB_SHA").lower()
        if not REPOSITORY_RE.fullmatch(repository):
            raise StatusError("GITHUB_REPOSITORY is not an owner/repository slug")
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise StatusError("GITHUB_SHA must be a full 40-character commit SHA")
        return cls(repository, run_id, attempt, head)

    def value(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "head_sha": self.head_sha,
        }

    def external_id(self) -> str:
        return canonical(self.value())


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise StatusError(f"{name} is required")
    return value


def positive_int(raw: object, label: str) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise StatusError(f"{label} must be an integer") from exc
    if value <= 0:
        raise StatusError(f"{label} must be positive")
    return value


def parse_time(raw: object, label: str) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise StatusError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StatusError(f"{label} is not RFC3339") from exc
    if parsed.tzinfo is None:
        raise StatusError(f"{label} has no timezone")
    return parsed.astimezone(timezone.utc)


def artifact_name_matches(name: object, identity: Identity) -> bool:
    match = ARTIFACT_NAME_RE.fullmatch(str(name))
    return bool(
        match
        and int(match.group(1)) == identity.run_id
        and int(match.group(2)) == identity.run_attempt
    )


def api(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    query: dict[str, object] | None = None,
) -> Any:
    if not shutil.which("gh"):
        raise StatusError("gh is not installed")
    command = ["gh", "api", path]
    encoded: str | None = None
    if method != "GET" or body is not None or query:
        command.extend(["--method", method])
    for key, value in (query or {}).items():
        command.extend(["-f", f"{key}={value}"])
    if body is not None:
        command.extend(["--input", "-"])
        encoded = canonical(body)
    try:
        result = subprocess.run(
            command,
            input=encoded,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StatusError(f"GitHub API call failed: {type(exc).__name__}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise StatusError(detail[-1] if detail else f"gh exited {result.returncode}")
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StatusError("GitHub API returned non-JSON output") from exc


def checks(identity: Identity) -> list[dict[str, Any]]:
    path = f"/repos/{identity.repository}/commits/{identity.head_sha}/check-runs"
    gathered: list[dict[str, Any]] = []
    seen: set[int] = set()
    for page in range(1, MAX_CHECK_PAGES + 1):
        response = api(
            path,
            query={"filter": "all", "per_page": CHECKS_PER_PAGE, "page": page},
        )
        rows = response.get("check_runs") if isinstance(response, dict) else None
        total = response.get("total_count") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise StatusError("Check API response has no check_runs array")
        for row in rows:
            if not isinstance(row, dict):
                raise StatusError("Check API response contains a non-object")
            row_id = positive_int(row.get("id"), "check ID")
            if row_id not in seen:
                seen.add(row_id)
                gathered.append(row)
        if isinstance(total, int) and len(gathered) >= total:
            return gathered
        if len(rows) < CHECKS_PER_PAGE:
            return gathered
    raise StatusError("Check pagination cap exhausted")


def check_identity(item: dict[str, Any]) -> dict[str, object] | None:
    raw = item.get("external_id")
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def matching_checks(identity: Identity) -> list[dict[str, Any]]:
    expected = identity.value()
    return [
        item for item in checks(identity)
        if item.get("name") == CHECK_NAME
        and str(item.get("head_sha", "")).lower() == identity.head_sha
        and check_identity(item) == expected
    ]


def check_body(
    identity: Identity,
    *,
    status: str,
    conclusion: str | None = None,
    summary: dict[str, object] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "name": CHECK_NAME,
        "head_sha": identity.head_sha,
        "external_id": identity.external_id(),
        "status": status,
        "output": {
            "title": CHECK_NAME,
            "summary": canonical(summary or {}),
        },
    }
    if conclusion is not None:
        body["conclusion"] = conclusion
        body["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return body


def create_check(identity: Identity, body: dict[str, object]) -> dict[str, Any]:
    result = api(f"/repos/{identity.repository}/check-runs", method="POST", body=body)
    if not isinstance(result, dict) or not result.get("id"):
        raise StatusError("Check creation returned no ID")
    return result


def update_check(identity: Identity, check_id: int, body: dict[str, object]) -> dict[str, Any]:
    result = api(
        f"/repos/{identity.repository}/check-runs/{check_id}",
        method="PATCH",
        body=body,
    )
    if not isinstance(result, dict):
        raise StatusError("Check update returned no object")
    return result


def require_one_check(identity: Identity) -> dict[str, Any]:
    found = matching_checks(identity)
    if len(found) != 1:
        raise StatusError(f"expected one exact {CHECK_NAME} Check, found {len(found)}")
    return found[0]


def summary_of(item: dict[str, Any]) -> dict[str, object] | None:
    output = item.get("output")
    raw = output.get("summary") if isinstance(output, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def require_readback(
    identity: Identity,
    *,
    status: str,
    conclusion: str | None,
    summary: dict[str, object],
) -> dict[str, Any]:
    item = require_one_check(identity)
    if item.get("status") != status:
        raise StatusError("Check readback status differs")
    if conclusion is not None and item.get("conclusion") != conclusion:
        raise StatusError("Check readback conclusion differs")
    if summary_of(item) != summary:
        raise StatusError("Check readback summary differs")
    return item


def producer_start(identity: Identity) -> int:
    if matching_checks(identity):
        raise StatusError("an exact Check already exists for this workflow attempt")
    summary: dict[str, object] = {"state": "running"}
    create_check(identity, check_body(identity, status="in_progress", summary=summary))
    require_readback(identity, status="in_progress", conclusion=None, summary=summary)
    return 0


def artifact_readback(identity: Identity, args: argparse.Namespace) -> dict[str, object]:
    run_readback(identity)
    artifact_id = positive_int(args.artifact_id, "artifact_id")
    item = api(f"/repos/{identity.repository}/actions/artifacts/{artifact_id}")
    if not isinstance(item, dict):
        raise StatusError("artifact API returned no object")
    workflow = item.get("workflow_run")
    if not isinstance(workflow, dict):
        raise StatusError("artifact has no workflow_run provenance")
    expected_name = str(args.artifact_name)
    expected_digest = str(args.artifact_digest).lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        expected_digest = "sha256:" + expected_digest
    if not SHA256_RE.fullmatch(expected_digest):
        raise StatusError("artifact_digest is not a sha256 digest")
    actual_bytes = positive_int(item.get("size_in_bytes"), "provider artifact size")
    created = str(item.get("created_at", ""))
    expires = str(item.get("expires_at", ""))
    parse_time(created, "artifact.created_at")
    if parse_time(expires, "artifact.expires_at") <= datetime.now(timezone.utc):
        raise StatusError("artifact is expired")
    if item.get("expired") is not False:
        raise StatusError("provider marks artifact expired")
    artifact_attempt = workflow.get("run_attempt")
    required = {
        "artifact ID": item.get("id") == artifact_id,
        "artifact name": item.get("name") == expected_name,
        "artifact digest": item.get("digest") == expected_digest,
        "workflow run": positive_int(workflow.get("id"), "artifact workflow run") == identity.run_id,
        "workflow head": str(workflow.get("head_sha", "")).lower() == identity.head_sha,
        "run/attempt-bound name": artifact_name_matches(expected_name, identity),
    }
    if artifact_attempt is not None:
        required["workflow attempt"] = (
            positive_int(artifact_attempt, "artifact workflow attempt") == identity.run_attempt
        )
    if args.artifact_bytes is not None:
        required["artifact bytes"] = actual_bytes == positive_int(args.artifact_bytes, "artifact_bytes")
    if args.artifact_created_at is not None:
        required["artifact creation"] = created == args.artifact_created_at
    if args.artifact_expires_at is not None:
        required["artifact expiry"] = expires == args.artifact_expires_at
    failed = [label for label, ok in required.items() if not ok]
    if failed:
        raise StatusError("artifact readback mismatch: " + ", ".join(failed))
    return {
        "artifact_id": artifact_id,
        "artifact_name": expected_name,
        "artifact_digest": expected_digest,
        "artifact_bytes": actual_bytes,
        "artifact_created_at": created,
        "artifact_expires_at": expires,
        "required_steps": REQUIRED_STEPS,
    }


def producer_complete(identity: Identity, args: argparse.Namespace) -> int:
    current = require_one_check(identity)
    if current.get("status") != "in_progress":
        raise StatusError("completion requires the attempt's in-progress Check")
    summary = artifact_readback(identity, args)
    update_check(
        identity,
        positive_int(current.get("id"), "check ID"),
        check_body(identity, status="completed", conclusion="success", summary=summary),
    )
    require_readback(identity, status="completed", conclusion="success", summary=summary)
    return 0


def failure_summary(reason: str) -> dict[str, object]:
    return {"signal": "backup-failure", "reason": reason}


def producer_fail(identity: Identity, reason: str) -> int:
    summary = failure_summary(reason)
    found = matching_checks(identity)
    if len(found) > 1:
        raise StatusError("multiple exact Checks make failure attribution ambiguous")
    body = check_body(identity, status="completed", conclusion="failure", summary=summary)
    if found:
        if found[0].get("conclusion") == "success":
            raise StatusError("refusing to replace an artifact-backed success Check")
        update_check(identity, positive_int(found[0].get("id"), "check ID"), body)
    else:
        create_check(identity, body)
    require_readback(identity, status="completed", conclusion="failure", summary=summary)
    return 0


NON_FAILING_CHECK_CONCLUSIONS = ("success", "neutral", "skipped")


def _parse_proof_request(args: argparse.Namespace) -> tuple[str, str]:
    """The one spelling of the controlled-failure proof arguments.

    Both sides of this seam take the same two arguments and must accept exactly
    the same spelling of them: the operator-side dispatch door below, and the
    in-run guards in controlled_failure_guards. They were parsed in two places,
    which is precisely the shape that produces a normalisation divergence -- a
    door that lowercased and an in-run guard that did not would dispatch a head
    the run then refuses, spending the very minutes the admission exists to
    protect. One parse, one pair of messages, no room to drift apart.

    NOT lowercased. The in-run guard refuses an uppercase head rather than
    normalising it, and this helper keeps that refusal rather than softening it
    on its way to a new caller.
    """
    head = str(args.expected_head or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise StatusError("--expected-head must be a lowercase 40-character commit SHA")
    proof_id = str(args.proof_id or "").strip()
    if not UUID_RE.fullmatch(proof_id):
        raise StatusError("--proof-id must be a canonical lowercase UUID")
    return proof_id, head


def _workflow_run_check_suites(
    identity: Identity,
    workflow_file: str,
    workflow_path: str,
) -> frozenset[int]:
    """Which check-suites on this head did ONE named workflow file produce?

    ASKED OF THE PROVIDER, AND OF NOTHING ELSE. GitHub holds the binding between
    a check-run and the workflow run that produced it: every check-run carries a
    ``check_suite``, and every workflow run reports the ``check_suite_id`` it
    writes into, alongside the ``path`` of the workflow file that ran and the
    ``head_sha`` it ran on. Those three are provider facts -- a creator cannot
    set any of them on a Check it posts -- so resolving a workflow's suites once,
    from the runs endpoint scoped to one workflow file and this one head, gives
    both decisions below an identity nobody outside that workflow can mint.

    ONE READER, TWO CALLERS, AND WHY IT TAKES ITS WORKFLOW AS AN ARGUMENT. The
    exclusion asks it about backup-nightly.yml and the positive evidence asks it
    about ci.yml. The alternative was a second paged reader with the same
    pagination, the same re-reads and the same fail-closed cap -- which is the
    duplicated-validation shape that produced this seam's earlier normalisation
    divergence. It is module-private and both callers below are module-private
    wrappers that pass this file's own literals; no caller of this module can
    name a workflow.

    THE ORDERED QUESTIONS, per returned run:
      1. Does the provider say this run came from ``workflow_path``? A run from
         any other workflow file contributes no suite, which is what stops a
         different workflow under the same Actions app from being counted.
      2. Does the provider say it ran on THIS head? The listing is already
         filtered by head, and the answer is re-read rather than assumed, which
         is what stops a run of this same workflow on another commit from
         contributing its suite.
      3. Is ``check_suite_id`` a positive integer? Anything else is not an
         identity and is skipped.

    FAIL-CLOSED IN BOTH DIRECTIONS, for both callers. An unreadable listing
    raises out of ``api`` and the dispatch refuses. A readable listing that names
    no run returns an empty set -- which for the exclusion means the seam's own
    earlier failure counts as ordinary red evidence, and for the positive
    evidence means no row can authenticate as the required check. Neither
    outcome can admit a spend; the cost of being wrong here is always a refusal.
    """
    path = (f"/repos/{identity.repository}/actions/workflows/"
            f"{workflow_file}/runs")
    suites: set[int] = set()
    seen: set[int] = set()
    for page in range(1, MAX_RUN_PAGES + 1):
        response = api(path, query={
            "head_sha": identity.head_sha,
            "per_page": RUNS_PER_PAGE,
            "page": page,
        })
        rows = response.get("workflow_runs") if isinstance(response, dict) else None
        total = response.get("total_count") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise StatusError("workflow run response has no workflow_runs array")
        for row in rows:
            if not isinstance(row, dict):
                raise StatusError("workflow run response contains a non-object")
            seen.add(positive_int(row.get("id"), "workflow run ID"))
            if row.get("path") != workflow_path:
                continue
            if str(row.get("head_sha", "")).lower() != identity.head_sha:
                continue
            suite = row.get("check_suite_id")
            if isinstance(suite, bool) or not isinstance(suite, int) or suite <= 0:
                continue
            suites.add(suite)
        if isinstance(total, int) and len(seen) >= total:
            return frozenset(suites)
        if len(rows) < RUNS_PER_PAGE:
            return frozenset(suites)
    raise StatusError("workflow run pagination cap exhausted")


def _seam_run_check_suites(identity: Identity) -> frozenset[int]:
    """The suites this seam's OWN workflow file produced on this head."""
    return _workflow_run_check_suites(
        identity, BACKUP_WORKFLOW_FILE, BACKUP_WORKFLOW_PATH)


def _required_check_suites(identity: Identity) -> frozenset[int]:
    """The suites the REQUIRED check's workflow file produced on this head."""
    return _workflow_run_check_suites(
        identity, REQUIRED_CHECK_WORKFLOW_FILE, REQUIRED_CHECK_WORKFLOW_PATH)


def _provider_suite_id(item: dict[str, Any], identity: Identity) -> int | None:
    """Which check-suite did GITHUB place this row in, if it is an Actions row?

    The one piece of identity on a check-run that its creator cannot choose, and
    the only thing either decision below is allowed to turn on. Returns the
    provider-assigned suite ID, or None when the row does not authenticate at
    all.

    THE ORDERED QUESTIONS. The answer must be yes at every one; the first no
    returns None and the caller treats the row as unauthenticated.

      1. Did the provider stamp a producer app object on the row at all? A row
         with no app is unattributed.
      2. Is that app the registered Actions app -- BOTH id and slug?
      3. Does the provider place this row on THIS head?
      4. Did the provider assign it a positive integer check-suite ID?

    NOTHING THE CREATOR WROTE IS ONE OF THE QUESTIONS -- not the name, not the
    title, not the output, not the ``external_id`` envelope. That is the whole
    point of this function, and it is the second time it has had to be learned.
    Deciding by NAME meant any creator who could attach the label "Backup
    artifact" hid a red Check. Deciding by the app stamp PLUS the envelope was no
    better: the stamp is shared with every ordinary Actions Check on the head,
    and the envelope is a string the creator supplies, so review forged a valid
    envelope under the shared stamp and had a red Check excluded. A label is not
    access control, and neither is a receipt the caller wrote.

    QUESTION 2 IS CHEAP NARROWING, NOT THE AUTHENTICATION. What the callers
    actually stand on is that the suite ID returned here can be compared against
    the suites a NAMED WORKFLOW FILE produced on this head, resolved from the
    provider's own run listing -- and the creator of a Check cannot choose its
    check-suite: GitHub assigns it from the installation and, for an Actions run,
    from the run itself.
    """
    app = item.get("app")
    if not isinstance(app, dict):
        return None
    if app.get("id") != ACTIONS_APP_ID or app.get("slug") != ACTIONS_APP_SLUG:
        return None
    if str(item.get("head_sha", "")).lower() != identity.head_sha:
        return None
    suite = item.get("check_suite")
    if not isinstance(suite, dict):
        return None
    suite_id = suite.get("id")
    if isinstance(suite_id, bool) or not isinstance(suite_id, int) or suite_id <= 0:
        return None
    return suite_id


def _is_seam_producer_check(
    item: dict[str, Any],
    identity: Identity,
    seam_suites: frozenset[int],
) -> bool:
    """Is this check-run the backup seam's OWN outcome, by PROVIDER-BACKED identity?

    One question on top of ``_provider_suite_id``: does GitHub itself place this
    row in a suite that ``_seam_run_check_suites`` resolved from a run of THIS
    workflow file on THIS head? The row's name is not consulted, here or there.

    THE BOUNDARY CASE THAT IS EASY TO GET WRONG: a run of a DIFFERENT workflow
    file in this repository carries the same Actions app stamp and is a real
    workflow run with a real suite -- and it is not this seam. ``seam_suites``
    is scoped by ``workflow_run.path`` precisely so that row stays red evidence.
    """
    suite_id = _provider_suite_id(item, identity)
    return suite_id is not None and suite_id in seam_suites


def _is_authentic_required_check(
    item: dict[str, Any],
    identity: Identity,
    required_suites: frozenset[int],
) -> bool:
    """Is this check-run the repository's REQUIRED check, by provider-backed identity?

    THE ORDERED QUESTIONS, and the order is the point:

      1. Does ``_provider_suite_id`` authenticate the row at all -- Actions app,
         this head, a provider-assigned suite?
      2. Is that suite one ``_required_check_suites`` resolved from a run of
         ci.yml on THIS head? A success from any other workflow file fails here.
      3. ONLY THEN: is the row's name the required check's name?

    WHY THE NAME IS CONSULTED AT ALL, AND WHY IT IS SAFE HERE. ci.yml can grow a
    second job tomorrow, and the repository's ruleset requires one specific
    context. Question 3 SELECTS among rows that questions 1 and 2 have already
    proved the provider attributes to this workflow's run on this commit; it can
    only ever narrow that set. It is the exact opposite of the retired name-only
    exclusion, where the name was the WHOLE test and attaching the label was
    enough to change the outcome. A caller who attaches "ops/ci.sh --strict" to
    a Check it posts still fails question 1 or 2 and supplies no evidence.

    THE BOUNDARY CASE THAT IS EASY TO GET WRONG: a genuine ci.yml run on a
    DIFFERENT commit has a genuine Actions suite. ``required_suites`` is resolved
    from a listing filtered to this head and re-read per row, so it carries no
    authority here.
    """
    suite_id = _provider_suite_id(item, identity)
    if suite_id is None or suite_id not in required_suites:
        return False
    return item.get("name") == REQUIRED_CHECK_NAME


def _require_head_checks_clean(identity: Identity) -> None:
    """Establish the fact the budget gate asks for, rather than assert it.

    ``local_checks_green`` is a required-true field on the github-actions-remote-ci
    gate -- the gate's own upstream spelling, unchanged here. A door that hands
    it a literal True has told the budget gate something it never checked, which
    makes the admission decorative. The authoritative owner of "how did this
    commit's checks conclude" is GitHub's own check-run list for the head -- a
    list this file already reads for a different question, so the door reads it
    for this one too, and REFUSES rather than returning a verdict anyone could
    pass around.

    GREEN IS TWO FACTS, NOT ONE, AND THE SECOND ONE IS WHY THIS WAS REOPENED.
    An earlier revision required only that no row on the head had failed. That
    is the ABSENCE of red, and absence is cheap: a head with one caller-created
    Check concluded ``neutral`` satisfied it. The repository's own requirement is
    positive and specific -- AGENTS.md: ruleset "main: CI must be green" requires
    the ``ops/ci.sh --strict`` status check -- so this now demands both:

      1. NO RED. Every check GitHub holds for this head has concluded, and none
         of them failed. The only rows set aside are those the PROVIDER places in
         a check-suite produced by this seam's own workflow file on this same
         head (see _is_seam_producer_check) -- never a row merely NAMED like one.
      2. A REAL GREEN. At least one row authenticates as the required check by
         provider-backed identity (see _is_authentic_required_check) and concluded
         REQUIRED_CHECK_CONCLUSION. A caller-created Check carrying that name, and
         a genuine success from any other workflow file, both fail this.

    Fact 2 is strictly stronger than fact 1 could ever be, and it is checked
    second so the more specific refusal message survives: a head with a red row
    is told which row, and a head with no authenticated strict run is told that.

    THE EXCLUSION IS NOT A HOLE IN FACT 2. Rows the seam wrote are set aside from
    the evidence entirely, so an excluded row can neither carry the head nor be
    the required check -- the seam's own workflow file is not ci.yml, so it could
    never authenticate as one anyway.

    Both listings fail closed on exhausted pagination, so an incomplete answer
    refuses here rather than reading as a clean head.
    """
    seam_suites = _seam_run_check_suites(identity)
    required_suites = _required_check_suites(identity)
    observed = [
        item for item in checks(identity)
        if not _is_seam_producer_check(item, identity, seam_suites)
    ]
    if not observed:
        raise StatusError(
            f"{identity.head_sha} carries no concluded checks to stand on")
    for item in observed:
        name = str(item.get("name"))
        if item.get("status") != "completed":
            raise StatusError(f"check {name!r} on {identity.head_sha} has not concluded")
        if item.get("conclusion") not in NON_FAILING_CHECK_CONCLUSIONS:
            raise StatusError(
                f"check {name!r} on {identity.head_sha} concluded "
                f"{item.get('conclusion')!r}")
    authentic = [
        item for item in observed
        if _is_authentic_required_check(item, identity, required_suites)
    ]
    if not authentic:
        raise StatusError(
            f"{identity.head_sha} carries no provider-authenticated "
            f"{REQUIRED_CHECK_NAME!r} check from "
            f"{REQUIRED_CHECK_WORKFLOW_PATH} to stand on")
    if not any(item.get("conclusion") == REQUIRED_CHECK_CONCLUSION
               for item in authentic):
        raise StatusError(
            f"the authenticated {REQUIRED_CHECK_NAME!r} check on "
            f"{identity.head_sha} did not conclude {REQUIRED_CHECK_CONCLUSION}")


def _metering_gate_digest(decision: dict[str, Any]) -> str:
    """An opaque, recomputable digest of the admission the GATE itself issued.

    The receipt has to record which admission this dispatch stands on without
    restating it in the gate's own verdict vocabulary -- a receipt key an
    auditor could read as the outcome is a label pretending to be authority.
    A digest cannot be read that way, and it is not weaker evidence: an auditor
    recomputes it from the gate's own answer for the same request and compares.

    ``decided_on`` is deliberately outside the digest and reported beside it, so
    the mark is stable for a given admission rather than changing with the
    calendar.
    """
    return hashlib.sha256(canonical({
        key: decision.get(key)
        for key in ("admitted", "authority", "gate", "platform", "policy_schema_version")
    }).encode("utf-8")).hexdigest()[:16]


def _dispatch_controlled_failure(proof_id: str, head: str) -> int:
    """The reviewed door that asks the budget gate first, then dispatches.

    MODULE-PRIVATE, and it takes two already-parsed strings rather than the
    argparse namespace. Its first revision was a public function that read
    attributes off a caller-supplied object, which is inside the threat model:
    an object whose attribute access runs caller code sits between the guards
    and the vendor POST. main() below is the only caller, and it hands over
    plain validated text.

    TWO LIFECYCLES IN ONE FILE, AND WHY. Every other command here runs INSIDE a
    backup-nightly run and reads its identity from GITHUB_*. This one runs on an
    operator's machine before any run exists, so it takes no identity from the
    environment and builds the only one it needs from the seam's own literals.
    It lives here rather than in a new script because the WR54 seam's contract --
    which repository, which ref, which proof-ID shape, which head -- is already
    stated here as literals, and a second file restating them would be a second
    place for them to drift.

    WHY IT EXISTS AT ALL. hooks/guard-unattended.py refuses a session-issued
    `gh workflow run` outright, and it is right to: the command text cannot show
    whether the spend was admitted. Its own docstring names the sanctioned shape
    instead -- "reviewed scripts perform their own in-process admission before
    reaching the vendor" -- which is the last thing this function does before the
    POST. It is NOT a general dispatcher: the workflow, the ref and the input
    names are literals, so nothing else can be started through it.

    ORDER MATTERS. The budget admission runs after the local guards and before
    the POST, so a refused proof never reaches GitHub and an admitted one is
    never stranded behind a guard that would have refused anyway.
    """
    # The head the caller approved must still be the head the run will check out.
    # Dispatching against a stale SHA produces a run whose own in-workflow guard
    # refuses, which spends Actions minutes to learn something readable here.
    branch = CONTROLLED_FAILURE_REF.removeprefix("refs/heads/")
    live = api(f"/repos/{CONTROLLED_FAILURE_REPOSITORY}/commits/{branch}")
    live_head = str(live.get("sha", "")).lower() if isinstance(live, dict) else ""
    if not re.fullmatch(r"[0-9a-f]{40}", live_head):
        raise StatusError(f"could not read the current {branch} head")
    if live_head != head:
        raise StatusError(
            f"--expected-head is not the current {branch} head ({live_head})"
        )

    identity = Identity(CONTROLLED_FAILURE_REPOSITORY, 1, 1, head)
    if head_carries_proof(identity, proof_id):
        raise StatusError("this proof ID is already carried by a Check on this head")
    _require_head_checks_clean(identity)

    policy_path = REPO / "ops/config/platform-metering.v1.json"
    try:
        decision = _authorize_metered_execution(
            json.loads(policy_path.read_text(encoding="utf-8")),
            "github-actions-remote-ci",
            # Established immediately above by _require_head_checks_clean,
            # which refuses unless BOTH halves hold: every check GitHub holds
            # for this head has concluded and none failed, AND at least one row
            # the PROVIDER attributes to a ci.yml run on this same head carries
            # the required check's name and concluded success. Both decisions
            # turn on the provider-assigned check-suite -- never on a row merely
            # NAMED like the seam's or like the required check, and never on a
            # copy of the seam's envelope. Never a literal standing in for a
            # fact nobody checked.
            {"candidate_sha": head, "local_checks_green": True},
        )
    except (_MeteringRefusal, ValueError, TypeError) as exc:
        raise StatusError(f"metered execution refused: {exc}") from exc
    # The gate raises rather than returning a refusal today, so this is belt and
    # braces -- but the POST is the irreversible half of this function, and it
    # should read its authority rather than assume the shape of it.
    if (not isinstance(decision, dict) or decision.get("admitted") is not True
            or decision.get("gate") != "github-actions-remote-ci"):
        raise StatusError("metered execution returned no admission for this gate")

    api(
        f"/repos/{CONTROLLED_FAILURE_REPOSITORY}/actions/workflows/"
        f"{BACKUP_WORKFLOW_FILE}/dispatches",
        method="POST",
        body={
            "ref": branch,
            "inputs": {
                "wr54_failure_proof_id": proof_id,
                "wr54_failure_proof_expected_head": head,
            },
        },
    )
    print(canonical({
        "state": "dispatched",
        "workflow": BACKUP_WORKFLOW_FILE,
        "ref": branch,
        "head_sha": head,
        "proof_id": proof_id,
        "metering_gate": decision.get("gate"),
        "metering_gate_digest": _metering_gate_digest(decision),
        "metering_decided_on": decision.get("decided_on"),
    }))
    return 0


def head_carries_proof(identity: Identity, proof_id: str) -> bool:
    """Has any Backup artifact Check on this head already carried this proof ID?

    Deliberately wider than "a failure Check": a Check in ANY state carrying the
    ID blocks reuse, because the question being answered is whether this exact
    proof has already been spent on this commit, and a half-written Check is
    still a spent one. ``checks`` fails closed when the provider cannot supply a
    complete answer, so an exhausted pagination cap refuses rather than
    reporting a clean head.
    """
    for item in checks(identity):
        if item.get("name") != CHECK_NAME:
            continue
        if str(item.get("head_sha", "")).lower() != identity.head_sha:
            continue
        summary = summary_of(item)
        if isinstance(summary, dict) and summary.get("proof_id") == proof_id:
            return True
    return False


def controlled_failure_guards(identity: Identity, args: argparse.Namespace) -> str:
    """Every helper-local guard for the controlled-failure seam, in order.

    Raises on the first failure, so a partly-checked request never reaches a
    write. Both commands call this: the read-only one to refuse before anything
    durable exists, and the writing one again at the write boundary, because
    time passes between them and only the second check is the one that counts.
    """
    event = required_env("GITHUB_EVENT_NAME")
    if event != CONTROLLED_FAILURE_EVENT:
        raise StatusError(
            f"controlled failure requires {CONTROLLED_FAILURE_EVENT}, not {event}"
        )
    actor = required_env("GITHUB_ACTOR")
    triggering = required_env("GITHUB_TRIGGERING_ACTOR")
    if actor != CONTROLLED_FAILURE_ACTOR or triggering != CONTROLLED_FAILURE_ACTOR:
        raise StatusError(
            "controlled failure requires both the actor and the triggering actor "
            f"to be {CONTROLLED_FAILURE_ACTOR}"
        )
    if identity.repository != CONTROLLED_FAILURE_REPOSITORY:
        raise StatusError(
            f"controlled failure is bound to {CONTROLLED_FAILURE_REPOSITORY}"
        )
    ref = required_env("GITHUB_REF")
    if ref != CONTROLLED_FAILURE_REF:
        raise StatusError(f"controlled failure is bound to {CONTROLLED_FAILURE_REF}")
    proof_id, expected_head = _parse_proof_request(args)
    if expected_head != identity.head_sha:
        raise StatusError("--expected-head does not equal this run's GITHUB_SHA")
    if run_artifacts(identity):
        raise StatusError("controlled failure requires a run with zero artifacts")
    if head_carries_proof(identity, proof_id):
        raise StatusError("this proof ID is already carried by a Check on this head")
    return proof_id


def validate_controlled_failure(identity: Identity, args: argparse.Namespace) -> int:
    """Read-only. Exit 0 only when every helper-local guard passes."""
    proof_id = controlled_failure_guards(identity, args)
    print(canonical({
        "state": "validated",
        "proof_id": proof_id,
        "head_sha": identity.head_sha,
        "run_id": identity.run_id,
        "run_attempt": identity.run_attempt,
    }))
    return 0


def controlled_failure(identity: Identity, args: argparse.Namespace) -> int:
    """Flip this run's own in-progress Check to the approved failure, then fail."""
    proof_id = controlled_failure_guards(identity, args)
    current = require_one_check(identity)
    if current.get("status") != "in_progress":
        raise StatusError("controlled failure requires this attempt's in-progress Check")
    if current.get("conclusion") is not None:
        raise StatusError("refusing to overwrite an already-concluded Check")
    summary: dict[str, object] = {
        "signal": "backup-failure",
        "reason": CONTROLLED_FAILURE_REASON,
        "proof_id": proof_id,
    }
    update_check(
        identity,
        positive_int(current.get("id"), "check ID"),
        check_body(identity, status="completed", conclusion="failure", summary=summary),
    )
    require_readback(identity, status="completed", conclusion="failure", summary=summary)
    print(canonical({
        "state": "failure",
        "signal": "backup-failure",
        "proof_id": proof_id,
        "reason": CONTROLLED_FAILURE_REASON,
    }))
    return CONTROLLED_FAILURE_EXIT


def run_artifacts(identity: Identity) -> list[dict[str, Any]]:
    response = api(f"/repos/{identity.repository}/actions/runs/{identity.run_id}/artifacts")
    rows = response.get("artifacts") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise StatusError("artifact listing has no artifacts array")
    total = response.get("total_count") if isinstance(response, dict) else None
    if isinstance(total, int) and total > len(rows):
        raise StatusError("artifact listing was truncated")
    return [row for row in rows if isinstance(row, dict)]


def summary_matches_artifact(
    identity: Identity,
    summary: dict[str, object] | None,
    artifacts: list[dict[str, Any]],
) -> bool:
    if not isinstance(summary, dict) or summary.get("required_steps") != REQUIRED_STEPS:
        return False
    try:
        artifact_id = positive_int(summary.get("artifact_id"), "artifact ID")
        expected_bytes = positive_int(summary.get("artifact_bytes"), "artifact bytes")
    except StatusError:
        return False
    selected = [item for item in artifacts if item.get("id") == artifact_id]
    if len(selected) != 1:
        return False
    item = selected[0]
    workflow = item.get("workflow_run")
    if not isinstance(workflow, dict):
        return False
    try:
        created = parse_time(item.get("created_at"), "artifact.created_at")
        expires = parse_time(item.get("expires_at"), "artifact.expires_at")
        actual_bytes = positive_int(item.get("size_in_bytes"), "artifact bytes")
        run_id = positive_int(workflow.get("id"), "artifact run ID")
        attempt = workflow.get("run_attempt")
        if attempt is not None and positive_int(attempt, "artifact run attempt") != identity.run_attempt:
            return False
    except StatusError:
        return False
    return all((
        item.get("expired") is False,
        expires > datetime.now(timezone.utc),
        created <= datetime.now(timezone.utc),
        item.get("name") == summary.get("artifact_name"),
        artifact_name_matches(item.get("name"), identity),
        item.get("digest") == summary.get("artifact_digest"),
        SHA256_RE.fullmatch(str(item.get("digest", ""))) is not None,
        actual_bytes == expected_bytes,
        item.get("created_at") == summary.get("artifact_created_at"),
        item.get("expires_at") == summary.get("artifact_expires_at"),
        run_id == identity.run_id,
        str(workflow.get("head_sha", "")).lower() == identity.head_sha,
    ))


def neutral_cancel(identity: Identity) -> int:
    if matching_checks(identity):
        raise StatusError("an exact Check already exists for this workflow attempt")
    summary: dict[str, object] = {"state": "disabled", "signal": None}
    create_check(
        identity,
        check_body(identity, status="completed", conclusion="neutral", summary=summary),
    )
    require_readback(identity, status="completed", conclusion="neutral", summary=summary)
    if run_artifacts(identity):
        raise StatusError("disabled workflow attempt already has artifacts")
    trusted_run_id = required_env("GITHUB_RUN_ID")
    if positive_int(trusted_run_id, "GITHUB_RUN_ID") != identity.run_id:
        raise StatusError("trusted run ID changed during neutral cancellation")
    cancel_path = f"/repos/{identity.repository}/actions/runs/{required_env('GITHUB_RUN_ID')}/cancel"
    api(cancel_path, method="POST")
    while True:
        time.sleep(60)


def run_readback(identity: Identity) -> dict[str, Any]:
    result = api(
        f"/repos/{identity.repository}/actions/runs/{identity.run_id}"
        f"/attempts/{identity.run_attempt}"
    )
    if not isinstance(result, dict):
        raise StatusError("workflow run API returned no object")
    run_id = result.get("id", result.get("databaseId"))
    attempt = result.get("run_attempt")
    head = result.get("head_sha", result.get("headSha", ""))
    if (
        positive_int(run_id, "provider run ID") != identity.run_id
        or positive_int(attempt, "provider run attempt") != identity.run_attempt
        or str(head).lower() != identity.head_sha
    ):
        raise StatusError("workflow run provenance mismatch")
    return result


def emit(state: str, signal: str | None, detail: str, proof_id: str | None = None) -> int:
    # proof_id is always present, null when there is none, so the supervised
    # incident writer binds the same proof from a structured field instead of
    # parsing it back out of the human-readable detail line.
    print(canonical({
        "state": state,
        "signal": signal,
        "detail": detail,
        "proof_id": proof_id,
    }))
    return {"disabled": 0, "success": 0, "failure": 1, "unknown": 2}[state]


def observer(identity: Identity) -> int:
    """Read terminal provider evidence; a separate caller decides delivery."""
    try:
        run = run_readback(identity)
        found = matching_checks(identity)
        artifacts = run_artifacts(identity)
    except StatusError as exc:
        return emit("unknown", None, str(exc))
    if run.get("status") != "completed" or len(found) != 1:
        return emit("unknown", None, "terminal run has no unique exact Check")
    item = found[0]
    conclusion = item.get("conclusion")
    summary = summary_of(item)
    if conclusion == "failure":
        if isinstance(summary, dict) and summary.get("signal") == "backup-failure":
            raw_proof = summary.get("proof_id")
            proof_id = (
                raw_proof
                if isinstance(raw_proof, str) and UUID_RE.fullmatch(raw_proof)
                else None
            )
            return emit(
                "failure",
                "backup-failure",
                str(summary.get("reason", "backup failed")),
                proof_id,
            )
        return emit("unknown", None, "failure Check has no exact backup-failure signal")
    if conclusion == "success":
        if summary_matches_artifact(identity, summary, artifacts):
            return emit("success", None, "artifact-backed producer Check succeeded")
        return emit("unknown", None, "success Check artifact metadata mismatch")
    if run.get("conclusion") == "cancelled" and conclusion == "neutral" and not artifacts:
        if summary in ({}, {"state": "disabled", "signal": None}):
            return emit("disabled", None, "cancelled neutral run with zero artifacts")
        return emit("unknown", None, "neutral Check summary mismatch")
    return emit("unknown", None, "terminal workflow and Check conclusions do not agree")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("producer-start")
    complete = sub.add_parser("producer-complete")
    complete.add_argument("--artifact-id", required=True)
    complete.add_argument("--artifact-name", required=True)
    complete.add_argument("--artifact-digest", required=True)
    complete.add_argument("--artifact-bytes")
    complete.add_argument("--artifact-created-at")
    complete.add_argument("--artifact-expires-at")
    failed = sub.add_parser("producer-fail")
    failed.add_argument("--reason", required=True)
    sub.add_parser("neutral-cancel")
    sub.add_parser("observe")
    for name in ("validate-controlled-failure", "controlled-failure",
                 "dispatch-controlled-failure"):
        proof = sub.add_parser(name)
        proof.add_argument("--proof-id", required=True)
        proof.add_argument("--expected-head", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        # The dispatch door runs BEFORE any run exists, so it must not demand a
        # run's identity from the environment. Every other command must.
        if args.command == "dispatch-controlled-failure":
            return _dispatch_controlled_failure(*_parse_proof_request(args))
        identity = Identity.environment()
        if args.command == "producer-start":
            return producer_start(identity)
        if args.command == "producer-complete":
            return producer_complete(identity, args)
        if args.command == "producer-fail":
            return producer_fail(identity, args.reason)
        if args.command == "neutral-cancel":
            return neutral_cancel(identity)
        if args.command == "observe":
            return observer(identity)
        if args.command == "validate-controlled-failure":
            return validate_controlled_failure(identity, args)
        if args.command == "controlled-failure":
            return controlled_failure(identity, args)
        raise StatusError("unknown command")
    except StatusError as exc:
        print(f"backup-workflow-status: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
