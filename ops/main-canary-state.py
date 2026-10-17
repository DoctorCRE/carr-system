#!/usr/bin/env python3
"""Read main CI health and the optional manual merge freeze."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

WORKFLOW = "main-canary.yml"
# Cancelled and skipped are absent on purpose — see the header.
CONCLUSIVE = ("success", "failure", "timed_out")


def repo_slug() -> str | None:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    p = subprocess.run(["git", "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    url = p.stdout.strip().removesuffix(".git")
    if url.startswith("git@") and ":" in url:
        return url.split(":", 1)[1]
    if "github.com/" in url:
        return url.split("github.com/", 1)[1]
    return None


def api(path: str) -> dict | None:
    """The Actions API, through gh when it is authenticated, else the token."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "carr-main-canary-state"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, OSError, ValueError):
            pass  # fall through to gh
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def state() -> dict:
    """green | red | unknown, with the evidence that produced it."""
    if os.environ.get("CARR_MAIN_FREEZE", "").strip().lower() == "on":
        return {"state": "red", "source": "CARR_MAIN_FREEZE=on",
                "detail": "a human froze merges by hand; clear the repository "
                          "variable CARR_MAIN_FREEZE to unfreeze"}
    slug = repo_slug()
    if not slug:
        return {"state": "unknown", "source": "no repository",
                "detail": "could not work out which GitHub repository this is"}
    data = api(f"/repos/{slug}/actions/workflows/{WORKFLOW}/runs"
               f"?branch=main&per_page=20&event=push")
    if data is None:
        return {"state": "unknown", "source": "api",
                "detail": f"could not read {WORKFLOW} runs (no token, or the API refused)"}
    runs = data.get("workflow_runs") or []
    for run in runs:            # newest first, as the API returns them
        if run.get("conclusion") in CONCLUSIVE:
            green = run["conclusion"] == "success"
            return {"state": "green" if green else "red",
                    "source": f"{WORKFLOW} run {run.get('run_number')}",
                    "sha": (run.get("head_sha") or "")[:12],
                    "url": run.get("html_url", ""),
                    "detail": ("main was green when the canary last concluded"
                               if green else
                               f"the main canary FAILED on {(run.get('head_sha') or '')[:12]}")}
    if runs:
        return {"state": "unknown", "source": WORKFLOW,
                "detail": f"the last {len(runs)} canary runs were all cancelled or "
                          "still running — a merge burst is in flight, no verdict yet"}
    return {"state": "unknown", "source": WORKFLOW,
            "detail": f"{WORKFLOW} has never concluded a run on main"}


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--require-green", action="store_true",
                    help="exit nonzero unless main is PROVABLY green")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    s = state()
    if a.json:
        print(json.dumps(s, indent=2))
    else:
        print(f"main: {s['state'].upper()}  ({s['source']})")
        print(f"  {s['detail']}")
        if s.get("url"):
            print(f"  {s['url']}")

    if not a.require_green:
        return 0
    if s["state"] == "green":
        return 0

    print()
    if s["state"] == "red":
        print("MERGES ARE FROZEN — main is red, and this lane will not merge onto it.")
        print("  THE MOVE: fix main, in its own change. The canary going green is the")
        print("  unfreeze; there is nothing to reset and no flag to clear.")
        print("  Open PRs failing a check they did not touch are victims of this same")
        print("  break — ops/inherited-from-main.py names it for them.")
    else:
        # UNKNOWN REFUSES TOO, and that is the Codex kill criterion honoured: an
        # unattended overnight merge that cannot establish main is green must not
        # proceed on the strength of not having looked. Declining costs one night.
        print("MERGES ARE HELD — main's state could not be established, so this lane")
        print("  declines rather than merging on an unchecked base.")
        print("  THE MOVE: run the main canary on main (workflow_dispatch) and re-run")
        print("  this lane once it concludes.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # A crash is an unknown state, and unknown never satisfies --require-green.
        print(f"main-canary-state: unknown — {exc!r}")
        sys.exit(1)
