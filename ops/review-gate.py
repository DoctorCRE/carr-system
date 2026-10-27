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


def approved(pr, files, comments):
    if SKIP_CI.search(pr.get("title") or ""):
        return False
    sha_line = f'Reviewed-SHA: {pr["head"]["sha"]}'
    # GitHub lists comments oldest first; the newest VERDICT naming the current head wins.
    verdicts = [lines[0] for lines in (c["body"].splitlines() for c in comments)
                if lines[:1] in (["VERDICT: APPROVE"], ["VERDICT: CHANGES"]) and sha_line in lines]
    if verdicts[-1:] == ["VERDICT: CHANGES"]:
        return False
    # Markdown-only PRs carry no runtime behaviour, so they merge on green CI at any size (Joe 2026-10-27).
    if files and len(files) == pr["changed_files"] and all(
            f.get(key, "x.md").lower().endswith(".md") for f in files for key in ("filename", "previous_filename")):
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
    def status(sha, state):
        api(f"{root}/statuses/{sha}", {"context": "review-gate", "state": state,
            "description": "No [skip ci] title; small safe diff or current-head approval required"})
    if "merge_group" in event:
        status(event["merge_group"]["head_sha"], "success")
        return
    number = event.get("number") or event["issue"]["number"]
    pr = api(f"{root}/pulls/{number}")
    if pr["base"]["ref"] != "main":
        return
    sha = pr["head"]["sha"]
    status(sha, "pending")
    files = api(f"{root}/pulls/{number}/files?per_page=100")
    comments = api(f"{root}/issues/{number}/comments?per_page=100")
    passed = approved(pr, files, comments)
    if api(f"{root}/pulls/{number}")["head"]["sha"] == sha:
        status(sha, "success" if passed else "failure")


if __name__ == "__main__":
    run(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()), os.environ["GITHUB_REPOSITORY"])
