"""Publish a SHA-bound review-gate commit status using trusted GitHub event data."""
import fnmatch
import re
import json
import os
import subprocess
import time
import tempfile
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


def approved(pr, files, reviews, reviewer_login, preserved=lambda sha: False):
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
    risky = any(f.get("status") == "added" and f.get("filename", "").startswith(("bin/", "hooks/", "ops/", ".github/workflows/")) for f in files) or any(fnmatch.fnmatchcase(f.get(key, "").lower(), pattern)
                for f in files for key in ("filename", "previous_filename") for pattern in RISKY)
    complete = len(files) == pr["changed_files"]
    if docs_only(pr, files) or (complete and pr["additions"] - pr["deletions"] <= 150 and not risky):
        return True
    author = (pr.get("user") or {}).get("login", "").lower()
    return any(reviewer_login and user == reviewer_login.lower() and user != author and r["state"] == "APPROVED" and (r.get("commit_id") == pr["head"]["sha"] or (r.get("commit_id") and preserved(r["commit_id"])))
               for user, r in latest.items())


def unchanged_merges(git_dir, base, reviewed, head):
    def git(*args):
        return subprocess.check_output(["git", "-C", git_dir, *args], stderr=subprocess.DEVNULL, timeout=120)
    try:
        commits = git("rev-list", "--first-parent", "--parents", f"{reviewed}..{head}").decode().splitlines()
        expected = head
        if not commits:
            return False
        for line in commits:
            parts = line.split()
            if len(parts) != 3 or parts[0] != expected:
                return False
            git("merge-base", "--is-ancestor", parts[2], base)
            expected = parts[1]
        if expected != reviewed:
            return False
        old_base = git("merge-base", base, reviewed).decode().strip()
        new_base = git("merge-base", base, head).decode().strip()
        opts = ("diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames")
        return git(*opts, old_base, reviewed) == git(*opts, new_base, head)
    except (subprocess.SubprocessError, OSError, UnicodeError):
        return False


def preserved_review(repo, number, base, reviewed, head):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(
            not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
            for sha in (base, reviewed, head)):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="review-gate-") as directory:
            subprocess.run(["git", "init", "--bare", "-q", directory], check=True, capture_output=True, timeout=30)
            subprocess.run(["git", "-C", directory, "fetch", "--quiet", "--no-tags",
                            f"https://github.com/{repo}.git", "refs/heads/main", f"refs/pull/{int(number)}/head"],
                           check=True, capture_output=True, timeout=150)
            return unchanged_merges(directory, base, reviewed, head)
    except (subprocess.SubprocessError, OSError, ValueError):
        return False


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
    passed = card_ok and approved(pr, files, reviews, os.environ.get("CARR_REVIEW_APP_LOGIN", ""),
        lambda reviewed: preserved_review(repo, number, pr["base"]["sha"], reviewed, sha))
    latest = api(f"{root}/pulls/{number}")
    if latest["head"]["sha"] == sha and all(latest.get(k) == pr.get(k) for k in ("body", "title", "auto_merge", "base")):
        description = ("No skip-CI title; small safe diff or current-head approval required" if card_ok else
                       "Missing PR body line: Card: WR<number>.S<number>.J<number>")
        status(sha, "success" if passed else "failure", description)
        print(description)
        return int(not passed)


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()), os.environ["GITHUB_REPOSITORY"]))
