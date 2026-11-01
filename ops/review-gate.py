"""Publish a SHA-bound review-gate commit status using trusted GitHub event data."""
import fnmatch
import re
import json
import os
import subprocess
import time
from pathlib import Path

RISKY = ("*launchagents*", "*.plist", "install-*.sh", "*/install-*.sh",
         "claude.md", "agents.md", "*/claude.md", "*/agents.md",
         ".claude/*", ".agents/*", "docs/agents/*",
         "*outlook*", "*hxstore*", "*hxprobe*", "*calendar*",
         ".github/workflows/deploy*", "migrations/*", "db/*", "*wrangler*", "tools/migrate.py")


# GitHub skips every push workflow for these title markers, so a merged PR would never deploy.
SKIP_CI = re.compile(r"\[(skip ci|ci skip|no ci|skip actions|actions skip)\]", re.I)


def docs_only(pr, files):
    return bool(files) and len(files) == pr["changed_files"] and all(
        f.get(key, "x.md").lower().endswith(".md") for f in files for key in ("filename", "previous_filename"))


def card_allowed(pr, files):
    return (pr.get("user", {}).get("type") == "Bot" or docs_only(pr, files) or
            any(re.fullmatch(r"Card: WR[0-9]+\.S[0-9]+\.J[0-9]+", line)
                for line in (pr.get("body") or "").splitlines()))


def review_exempt(pr, files):
    risky = any(f.get("status") == "added" and f.get("filename", "").startswith(("bin/", "hooks/", "ops/", ".github/workflows/")) for f in files) or any(fnmatch.fnmatchcase(f.get(key, "").lower(), pattern)
                for f in files for key in ("filename", "previous_filename") for pattern in RISKY)
    complete = len(files) == pr["changed_files"]
    return complete and pr["additions"] - pr["deletions"] <= 150 and not risky


def approved(pr, files, reviews, reviewer_login):
    if reviewer_login and not reviewer_login.endswith("[bot]"):
        return False
    if SKIP_CI.search(pr.get("title") or ""):
        return False
    latest = {}
    for review in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        user = (review.get("user") or {}).get("login")
        state = review.get("state")
        if user and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[user.lower()] = review
    current_review = latest.get(reviewer_login.lower(), {}) if reviewer_login else {}
    if current_review.get("state") == "CHANGES_REQUESTED" and current_review.get("commit_id") == pr["head"]["sha"]:
        return False
    if review_exempt(pr, files):
        return True
    author = (pr.get("user") or {}).get("login", "").lower()
    return any(reviewer_login and user == reviewer_login.lower() and user != author and (r.get("user") or {}).get("type") == "Bot" and r["state"] == "APPROVED" and r.get("commit_id") == pr["head"]["sha"]
               for user, r in latest.items())


def api(path, payload=None):
    time.sleep(2)
    args = ["gh", "api", path]
    if payload is None:
        args += ["--paginate", "--slurp"]
    else:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload else None,
                            text=True, capture_output=True, check=True)
    data = json.loads(result.stdout)
    return data if payload else (sum(data, []) if data and isinstance(data[0], list) else data[0])


def run(event, repo):
    root = f"repos/{repo}"
    def status(sha, state, description="No skip-CI title; small safe diff or current-head approval required"):
        api(f"{root}/statuses/{sha}", {"context": "review-gate", "state": state,
            "description": description})
    if "merge_group" in event:
        status(event["merge_group"]["head_sha"], "success")
        return
    number = event.get("number") or (event.get("pull_request") or event["issue"])["number"]
    pr = api(f"{root}/pulls/{number}")
    if pr["base"]["ref"] != "main":
        return
    sha = pr["head"]["sha"]
    status(sha, "pending")
    files = api(f"{root}/pulls/{number}/files?per_page=100")
    reviews = api(f"{root}/pulls/{number}/reviews?per_page=100")
    card_ok = card_allowed(pr, files)
    reviewer_login = os.environ.get("CARR_REVIEW_APP_LOGIN", "").strip()
    passed = card_ok and approved(pr, files, reviews, reviewer_login)
    latest = api(f"{root}/pulls/{number}")
    if latest["head"]["sha"] == sha and all(latest.get(k) == pr.get(k) for k in ("body", "title", "base")):
        description = ("No skip-CI title; small safe diff or current-head approval required" if card_ok else
                       "Missing PR body line: Card: WR<number>.S<number>.J<number>")
        if card_ok and not reviewer_login and not review_exempt(pr, files):
            description = "Missing CARR_REVIEW_APP_LOGIN; review-required PR cannot pass"
        if reviewer_login and not reviewer_login.endswith("[bot]"):
            description = "CARR_REVIEW_APP_LOGIN must name an App bot ending in [bot]"
        status(sha, "success" if passed else "failure", description)
        print(description)
        return int(not passed)


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()), os.environ["GITHUB_REPOSITORY"]))
