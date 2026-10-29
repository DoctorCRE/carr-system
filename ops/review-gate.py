"""Publish a SHA-bound review-gate commit status using trusted GitHub event data."""
import fnmatch
import re
import json
import os
import subprocess
import time
from pathlib import Path

RISKY = ("*launchagents*", "*.plist", "install-*.sh", "*/install-*.sh",
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


def approved(pr, files, comments):
    if SKIP_CI.search(pr.get("title") or ""):
        return False
    sha_line = f'Reviewed-SHA: {pr["head"]["sha"]}'
    # The newest verdict naming the current head wins across both sources.
    verdicts = [lines[0] for lines in ((c.get("body") or "").splitlines() for c in comments)
                if len(lines) >= 3 and lines[0] in ("VERDICT: APPROVE", "VERDICT: CHANGES")
                and lines[1] == sha_line and lines[2].startswith("Reviewer: ")
                and lines[2][10:].strip()]
    if verdicts[-1:] == ["VERDICT: CHANGES"]:
        return False
    # Markdown-only PRs carry no runtime behaviour, so they merge on green CI at any size (Joe 2026-10-27).
    if docs_only(pr, files):
        return True
    risky = any(f.get("status") == "added" and f.get("filename", "").startswith(("bin/", "hooks/", "ops/", ".github/workflows/")) for f in files) or any(fnmatch.fnmatchcase(f.get(key, "").lower(), pattern)
                for f in files for key in ("filename", "previous_filename") for pattern in RISKY)
    if len(files) == pr["changed_files"] and pr["additions"] - pr["deletions"] <= 150 and not risky:
        return True
    return verdicts[-1:] == ["VERDICT: APPROVE"]


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
    comments = api(f"{root}/issues/{number}/comments?per_page=100")
    reviews = api(f"{root}/pulls/{number}/reviews?per_page=100")
    comments = sorted(comments + [r for r in reviews if r.get("state") not in ("DISMISSED", "PENDING")], key=lambda item: item.get("submitted_at") or item.get("created_at") or "")
    card_ok = card_allowed(pr, files)
    passed = card_ok and approved(pr, files, comments)
    latest = api(f"{root}/pulls/{number}")
    if latest["head"]["sha"] == sha and latest.get("body") == pr.get("body"):
        description = ("No skip-CI title; small safe diff or current-head approval required" if card_ok else
                       "Missing PR body line: Card: WR<number>.S<number>.J<number>")
        status(sha, "success" if passed else "failure", description)
        print(description)
        return int(not passed)


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()), os.environ["GITHUB_REPOSITORY"]))
