#!/usr/bin/env python3
"""Collect public CARR source evidence without admitting or launching a job."""
import argparse
import hashlib
import json
import re
import subprocess
import sys

REPO = "DoctorCRE/carr-system"
SHA = re.compile(r"[0-9a-f]{40}")


def positive_int(value):
    return type(value) is int and value > 0


def collect(run_id, read):
    """Read current GitHub metadata and return a candidate or explicit refusal."""
    if not positive_int(run_id):
        raise ValueError("source run id must be a positive integer")

    def refused(reason):
        return {"schema": "carr-source-ci-review-candidate.v1", "state": "refused",
                "source_run_id": run_id, "reason": reason}

    run = read(f"actions/runs/{run_id}")
    if not isinstance(run, dict):
        return refused("malformed-source-run")
    workflow = read("actions/workflows/ci.yml")
    if (not isinstance(workflow, dict) or not positive_int(workflow.get("id"))
            or run.get("workflow_id") != workflow["id"] or run.get("id") != run_id
            or run.get("event") != "pull_request"
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        return refused("not-successful-source-ci")
    for field in ("repository", "head_repository"):
        if not isinstance(run.get(field), dict) or run[field].get("full_name") != REPO:
            return refused("foreign-source-repository")
    sha = run.get("head_sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha) or not positive_int(run.get("run_attempt")):
        return refused("malformed-source-identity")
    jobs = read(f"actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100")
    if (not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list)
            or jobs.get("total_count") != len(jobs["jobs"]) or len(jobs["jobs"]) >= 100):
        return refused("incomplete-source-jobs")
    aggregate = [job for job in jobs["jobs"] if isinstance(job, dict)
                 and job.get("name") == "ops/ci.sh --strict"]
    if (len(aggregate) != 1 or aggregate[0].get("status") != "completed"
            or aggregate[0].get("conclusion") != "success"):
        return refused("source-aggregate-not-green")
    associations = run.get("pull_requests")
    if associations == []:
        associations = read(f"commits/{sha}/pulls?per_page=100")
        if not isinstance(associations, list) or len(associations) >= 100:
            return refused("incomplete-pr-association")
    if (not isinstance(associations, list) or len(associations) != 1
            or not isinstance(associations[0], dict)
            or not positive_int(associations[0].get("number"))):
        return refused("missing-or-ambiguous-pr-association")
    number = associations[0]["number"]
    pr = read(f"pulls/{number}")
    if (not isinstance(pr, dict) or pr.get("number") != number
            or pr.get("state") != "open" or pr.get("draft") is not False):
        return refused("pr-not-ready")
    head, base = pr.get("head"), pr.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        return refused("malformed-pr")
    for part in (head, base):
        if not isinstance(part.get("repo"), dict) or part["repo"].get("full_name") != REPO:
            return refused("foreign-pr-repository")
    if head.get("sha") != sha or base.get("ref") != "main":
        return refused("stale-or-wrong-base")
    key = hashlib.sha256(f"{REPO}:{number}:{sha}:1".encode()).hexdigest()
    return {
        "schema": "carr-source-ci-review-candidate.v1", "state": "candidate",
        "repository": REPO, "pull_request": number, "head_sha": sha,
        "review_round": 1, "request_key": key,
        "source_run_id": run_id, "source_run_attempt": run["run_attempt"],
    }


def github_read(path):
    result = subprocess.run(
        ["gh", "api", f"repos/{REPO}/{path}"], capture_output=True,
        text=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError("GitHub source read failed")
    if len(result.stdout) > 2_000_000:
        raise ValueError("GitHub source response exceeds limit")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(collect(args.run_id, github_read), sort_keys=True))
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"source-ci-review: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
