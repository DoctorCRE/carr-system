#!/usr/bin/env python3
"""recovery-point.py — how old is the newest backup, across BOTH backup paths.

WHY THIS EXISTS (2026-08-21). bin/nightly.sh computed the recovery point from
one line:

    newest_backup="$(ls -t "$REPO"/backups/*.sql.age 2>/dev/null | head -1)"

That counts the LOCAL dump only. There are two backup paths, and Program 4
built the second one on purpose so the backup of last resort would not depend
on the one machine everything else depends on:

  · LOCAL  — bin/nightly.sh -> bin/backup-dump.sh on Joe's Mac, archived to R2,
             reading CARR_DB_BACKUP_URL from ~/.config/carr/db.env.
  · CLOUD  — .github/workflows/backup-nightly.yml, the same script unmodified,
             same age public key, stored as a 90-day workflow artifact, reading
             BACKUP_DATABASE_URL as a GitHub Actions secret.

THE FALSE ALARM THIS REMOVES, measured 2026-08-21. The local path had no
credential, so the newest file in backups/ was four and a half days old and the
chain reported "newest backup is 104h old, objective is 24h" every run. Mean-
while the cloud workflow had succeeded that morning at 03:31 and every morning
before it. The real recovery point was about twelve hours, comfortably inside
the objective, and every local signal said otherwise.

Worse, the alarm pointed at a fix that would have made the system weaker.
migrations/0119_backup_role.sql states the rule on the role itself: the backup
credential is "held through BACKUP_DATABASE_URL, a GitHub Actions secret, never
on Joe's Mac and never in this repo." Adding CARR_DB_BACKUP_URL locally to
silence the alarm would have put the credential on the exact machine the second
path exists to be independent of. An alarm that is loudest when you are safe,
and whose obvious remedy undoes the isolation, is worse than no alarm.

THREE STATES, NEVER TWO. Each path reports fresh, stale, or UNKNOWN, and
unknown is never quietly folded into either of the others:

  · a path with no backup is a real gap and says so.
  · a path that is unconfigured BY DESIGN (local, on a machine that
    deliberately holds no credential) is not a gap; it is a path that was
    never meant to run here.
  · a path we could not ask — gh missing, gh not authenticated, the API
    unreachable — is UNKNOWN. It is not treated as fresh, because that would
    hide a genuinely dead workflow, and it is not treated as a gap, because
    that recreates the false alarm one layer up.

The overall recovery point is the NEWEST across the paths that answered. If no
path answered, the answer is unknown and the exit code says so rather than
inventing a number.

NO INTERACTIVE CREDENTIAL (rule 847f9995). The cloud read goes through `gh`,
which holds a stored token. If that token is absent or expired the read returns
UNKNOWN and the caller degrades; nothing here ever prompts, and nothing here
ever handles the backup credential itself.

Usage:
  ops/recovery-point.py                 # human-readable report
  ops/recovery-point.py --json          # machine-readable
  ops/recovery-point.py --hours         # just the age in whole hours, or the
                                        # word "unknown"; what nightly.sh reads

Exit codes:
  0  recovery point is within the objective
  1  recovery point is OUT of contract (a real, verified gap)
  2  unknown — no path could be read; do not conclude anything either way
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.github_reader import GitHubReader, GitHubUnreadable  # noqa: E402
WORKFLOW = "backup-nightly.yml"
RPO_HOURS = 24  # Joe's accepted objective, 2026-08-13.
ARTIFACTS_PER_PAGE = 100
CHECKS_PER_PAGE = 100
MAX_CANDIDATES = 50
MAX_PAGES = 10
MAX_CHECK_PAGES = 5
CHECK_NAME = "Backup artifact"
REQUIRED_STEPS = ["dump", "encrypt", "upload", "readback"]
ARTIFACT_NAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+-run-([1-9][0-9]*)-attempt-([1-9][0-9]*)(?:\.sql\.age)?$"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_hours(when: datetime) -> float:
    return (_now() - when).total_seconds() / 3600.0


def local_path(repo: str = REPO) -> dict:
    """Newest local encrypted dump, or why there is none.

    An absent CARR_DB_BACKUP_URL is reported as 'unconfigured' rather than as a
    failure: on Joe's Mac that absence is the design (see 0119_backup_role.sql),
    and calling it a gap is what produced the false alarm this module removes.
    """
    files = sorted(glob.glob(os.path.join(repo, "backups", "*.sql.age")),
                   key=os.path.getmtime, reverse=True)
    configured = bool(os.environ.get("CARR_DB_BACKUP_URL"))
    if not files:
        return {"path": "local", "state": "none", "configured": configured,
                "detail": "no encrypted dump in backups/"}
    newest = files[0]
    when = datetime.fromtimestamp(os.path.getmtime(newest), tz=timezone.utc)
    return {"path": "local", "state": "present", "configured": configured,
            "at": when.isoformat().replace("+00:00", "Z"),
            "age_hours": round(_age_hours(when), 1),
            "detail": os.path.basename(newest)}


class CloudUnknown(RuntimeError):
    """The provider could not give a conclusive answer."""


def _timestamp(raw: object, label: str) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise CloudUnknown(f"provider omitted {label}")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CloudUnknown(f"provider returned invalid {label}") from exc
    if value.tzinfo is None:
        raise CloudUnknown(f"provider returned timezone-free {label}")
    return value.astimezone(timezone.utc)


def _positive_int(raw: object, label: str) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise CloudUnknown(f"provider returned invalid {label}") from exc
    if value <= 0:
        raise CloudUnknown(f"provider returned non-positive {label}")
    return value


def _repository(repo: str) -> str:
    configured = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if configured:
        return configured
    try:
        remote = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=repo, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CloudUnknown(f"cannot identify repository: {type(exc).__name__}") from exc
    value = remote.stdout.strip()
    match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?$", value)
    if remote.returncode or match is None:
        raise CloudUnknown("cannot identify GitHub repository")
    return match.group(1)


def _gh_api(path: str, repo: str, query: dict[str, object] | None = None) -> object:
    try:
        return GitHubReader(cwd=repo, timeout=30).api(path, fields=query)
    except GitHubUnreadable as exc:
        raise CloudUnknown(str(exc)) from exc


def _array(response: object, key: str) -> list[dict]:
    rows = response.get(key) if isinstance(response, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise CloudUnknown(f"provider response has no valid {key} array")
    return rows


def _identity(repository: str, run: dict) -> dict[str, object]:
    run_id = _positive_int(run.get("id", run.get("databaseId")), "run ID")
    attempt = _positive_int(run.get("run_attempt"), "run attempt")
    head = str(run.get("head_sha", run.get("headSha", ""))).lower()
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise CloudUnknown("provider returned invalid run head SHA")
    return {
        "repository": repository,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_sha": head,
    }


def _summary(item: dict) -> dict | None:
    output = item.get("output")
    raw = output.get("summary") if isinstance(output, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _artifact_name_matches(name: object, expected: dict[str, object]) -> bool:
    match = ARTIFACT_NAME_RE.fullmatch(str(name))
    return bool(
        match
        and int(match.group(1)) == expected["run_id"]
        and int(match.group(2)) == expected["run_attempt"]
    )


def _check_rows(repository: str, head_sha: str, repo: str) -> list[dict]:
    path = f"/repos/{repository}/commits/{head_sha}/check-runs"
    gathered: list[dict] = []
    seen: set[int] = set()
    for page in range(1, MAX_CHECK_PAGES + 1):
        response = _gh_api(
            path,
            repo,
            {"filter": "all", "per_page": CHECKS_PER_PAGE, "page": page},
        )
        rows = _array(response, "check_runs")
        total = response.get("total_count") if isinstance(response, dict) else None
        before = len(gathered)
        for row in rows:
            row_id = _positive_int(row.get("id"), "check ID")
            if row_id not in seen:
                seen.add(row_id)
                gathered.append(row)
        if isinstance(total, int) and len(gathered) >= total:
            return gathered
        if len(rows) < CHECKS_PER_PAGE:
            return gathered
        if len(gathered) == before:
            raise CloudUnknown("Check pagination did not advance")
    raise CloudUnknown("Check pagination cap exhausted")


def _run_attempt_details(repository: str, run_id: int, attempt: int, repo: str) -> dict:
    response = _gh_api(
        f"/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}", repo
    )
    if isinstance(response, dict) and response.get("id") is not None:
        return response
    # The subprocess fixture uses the list envelope for this endpoint too.
    rows = _array(response, "workflow_runs")
    selected = [
        row for row in rows
        if row.get("id", row.get("databaseId")) == run_id
        and row.get("run_attempt") == attempt
    ]
    if len(selected) != 1:
        raise CloudUnknown("provider returned no unique workflow run attempt")
    return selected[0]


def _verified_artifact(
    repository: str,
    run: dict,
    artifact: dict,
    repo: str,
) -> dict | None:
    expected = _identity(repository, run)
    check_rows = _check_rows(repository, str(expected["head_sha"]), repo)
    matches = []
    for item in check_rows:
        if (
            item.get("name") != CHECK_NAME
            or item.get("status") != "completed"
            or item.get("conclusion") != "success"
            or str(item.get("head_sha", "")).lower() != expected["head_sha"]
        ):
            continue
        try:
            external = json.loads(item.get("external_id", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if external == expected:
            matches.append(item)
    if len(matches) != 1:
        return None
    summary = _summary(matches[0])
    if not isinstance(summary, dict) or summary.get("required_steps") != REQUIRED_STEPS:
        return None
    try:
        artifact_id = _positive_int(summary.get("artifact_id"), "artifact ID")
        expected_bytes = _positive_int(summary.get("artifact_bytes"), "artifact bytes")
    except CloudUnknown:
        return None
    if artifact.get("id") != artifact_id:
        return None
    workflow_run = artifact.get("workflow_run")
    if not isinstance(workflow_run, dict):
        return None
    created_raw = artifact.get("created_at")
    expires_raw = artifact.get("expires_at")
    try:
        created = _timestamp(created_raw, "artifact.created_at")
        expires = _timestamp(expires_raw, "artifact.expires_at")
        actual_bytes = _positive_int(artifact.get("size_in_bytes"), "artifact size")
        artifact_run = _positive_int(workflow_run.get("id"), "artifact run ID")
    except CloudUnknown:
        return None
    attempt_value = workflow_run.get("run_attempt")
    comparisons = (
        artifact.get("expired") is False,
        expires > _now(),
        created <= _now(),
        artifact.get("name") == summary.get("artifact_name"),
        artifact.get("digest") == summary.get("artifact_digest"),
        actual_bytes == expected_bytes,
        created_raw == summary.get("artifact_created_at"),
        expires_raw == summary.get("artifact_expires_at"),
        artifact_run == expected["run_id"],
        str(workflow_run.get("head_sha", "")).lower() == expected["head_sha"],
        _artifact_name_matches(artifact.get("name"), expected),
    )
    if not all(comparisons) or (
        attempt_value is not None
        and _positive_int(attempt_value, "artifact run attempt") != expected["run_attempt"]
    ):
        return None
    return {
        "path": "cloud",
        "state": "present",
        "at": created_raw,
        "age_hours": round(_age_hours(created), 1),
        "artifact_id": artifact_id,
        "detail": (
            f"artifact {artifact_id} from workflow run {expected['run_id']} "
            f"attempt {expected['run_attempt']}"
        ),
    }


def cloud_path(workflow: str = WORKFLOW, repo: str = REPO) -> dict:
    """Newest artifact whose successful named Check proves exact provenance."""
    if not shutil.which("gh"):
        return {"path": "cloud", "state": "unknown",
                "detail": "gh is not installed; cannot read the workflow"}
    verified: list[dict] = []
    try:
        repository = _repository(repo)
        seen: set[int] = set()
        candidates = 0
        saw_backup_like = False
        for page in range(1, MAX_PAGES + 1):
            response = _gh_api(
                f"/repos/{repository}/actions/artifacts",
                repo,
                {"per_page": ARTIFACTS_PER_PAGE, "page": page},
            )
            artifacts = _array(response, "artifacts")
            total = response.get("total_count") if isinstance(response, dict) else None
            before = len(seen)
            for artifact in artifacts:
                artifact_id = _positive_int(artifact.get("id"), "artifact ID")
                if artifact_id in seen:
                    continue
                seen.add(artifact_id)
                name = str(artifact.get("name", ""))
                if not (name.startswith("carr-backup-") or name.startswith("carr-")):
                    continue
                # A legacy or malformed backup-looking artifact is incomplete
                # provider evidence. It makes absence unknown, never proven.
                saw_backup_like = True
                workflow_run = artifact.get("workflow_run")
                if not isinstance(workflow_run, dict):
                    continue
                run_id = _positive_int(workflow_run.get("id"), "artifact run ID")
                name_match = ARTIFACT_NAME_RE.fullmatch(name)
                if name_match is None:
                    continue
                artifact_name_run = _positive_int(name_match.group(1), "artifact-name run ID")
                artifact_name_attempt = _positive_int(
                    name_match.group(2), "artifact-name run attempt"
                )
                if artifact_name_run != run_id:
                    continue
                run = _run_attempt_details(
                    repository, run_id, artifact_name_attempt, repo
                )
                if run.get("path") not in (workflow, f".github/workflows/{workflow}"):
                    continue
                candidates += 1
                if candidates > MAX_CANDIDATES:
                    raise CloudUnknown("artifact candidate cap exhausted")
                candidate = _verified_artifact(repository, run, artifact, repo)
                if candidate is not None:
                    verified.append(candidate)
            exhausted = (
                (isinstance(total, int) and len(seen) >= total)
                or len(artifacts) < ARTIFACTS_PER_PAGE
            )
            if exhausted:
                if verified:
                    return min(verified, key=lambda item: item["age_hours"])
                if saw_backup_like:
                    raise CloudUnknown("backup-like artifacts lack complete Check provenance")
                return {"path": "cloud", "state": "none",
                        "detail": f"no artifact from {workflow} on record"}
            if len(seen) == before:
                raise CloudUnknown("artifact pagination did not advance")
        raise CloudUnknown("artifact page cap exhausted")
    except CloudUnknown as exc:
        if verified:
            newest = min(verified, key=lambda item: item["age_hours"])
            if newest["age_hours"] < RPO_HOURS:
                newest["detail"] += "; later provider scan was inconclusive"
                return newest
        return {"path": "cloud", "state": "unknown", "detail": str(exc)}


def assess(paths: list[dict], rpo_hours: int = RPO_HOURS) -> dict:
    """Combine the paths into one answer, keeping unknown separate from stale."""
    present = [p for p in paths if p["state"] == "present"]
    unknown = [p for p in paths if p["state"] == "unknown"]
    if not present:
        # Nothing answered with a real backup. If any path merely could not be
        # read, that is unknown; if every path answered "none", that is a gap.
        verdict = "unknown" if unknown else "gap"
        return {"verdict": verdict, "age_hours": None, "newest_path": None,
                "objective_hours": rpo_hours, "paths": paths}
    newest = min(present, key=lambda p: p["age_hours"])
    if newest["age_hours"] < rpo_hours:
        verdict = "ok"
    elif unknown:
        return {"verdict": "unknown", "age_hours": None, "newest_path": None,
                "objective_hours": rpo_hours, "paths": paths}
    else:
        verdict = "out_of_contract"
    return {"verdict": verdict, "age_hours": newest["age_hours"],
            "newest_path": newest["path"], "objective_hours": rpo_hours,
            "paths": paths}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--hours", action="store_true",
                    help="print only the age in whole hours, or 'unknown'")
    args = ap.parse_args(argv)

    report = assess([local_path(), cloud_path()])

    if args.hours:
        age = report["age_hours"]
        print("unknown" if age is None else str(int(age)))
    elif args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for p in report["paths"]:
            if p["state"] == "present":
                print(f"  {p['path']:6s} {p['age_hours']:>6.1f}h  {p['detail']}")
            elif p["path"] == "local" and not p.get("configured", False):
                print(f"  {p['path']:6s}     --  not configured on this machine "
                      f"(by design; see migrations/0119_backup_role.sql)")
            else:
                print(f"  {p['path']:6s}  {p['state'].upper():>7s}  {p['detail']}")
        if report["verdict"] == "unknown":
            print("recovery point: UNKNOWN — no path could be read; "
                  "this is not evidence of a gap and not evidence of a backup")
        elif report["verdict"] == "gap":
            print("recovery point: NO BACKUP on any path — a real gap")
        else:
            print(f"recovery point: {report['age_hours']:.1f}h via "
                  f"{report['newest_path']}, objective {report['objective_hours']}h "
                  f"— {'OK' if report['verdict'] == 'ok' else 'OUT OF CONTRACT'}")

    return {"ok": 0, "out_of_contract": 1, "gap": 1, "unknown": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
