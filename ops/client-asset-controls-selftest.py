#!/usr/bin/env python3
"""Executable seeded refusals for the five client-asset control predicates."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.client_asset_controls import (AssetControlRefusal, reject_weekly_quote_tweets,
                                       require_asset_tier, require_declined_and_why,
                                       require_search_commentary, require_supersession,
                                       write_artifact_atomically)

passes = 0
fails: list[str] = []


def check(label: str, fn, *, refuses: bool) -> None:
    global passes
    try:
        fn()
        refused = False
    except AssetControlRefusal:
        refused = True
    if refused == refuses:
        passes += 1
    else:
        fails.append(label)


check("search packet rejects empty findings", lambda: require_search_commentary(
    {"findings": [], "confirmations": [["source", "confirmed"]]}), refuses=True)
check("search packet rejects empty confirmations", lambda: require_search_commentary(
    {"findings": [["source", "finding"]], "confirmations": []}), refuses=True)
check("search packet accepts both market blocks", lambda: require_search_commentary(
    {"findings": [["source", "finding"]], "confirmations": [["source", "confirmed"]]}), refuses=False)
check("client recommendation rejects missing declined block",
      lambda: require_declined_and_why({"recommendation": {}}), refuses=True)
check("client recommendation accepts explicit declined block",
      lambda: require_declined_and_why({"declined_and_why": [{"option": "A", "why": "too small"}]}), refuses=False)
check("asset creation rejects untiered asset", lambda: require_asset_tier({"slug": "x"}), refuses=True)
check("asset creation accepts tier", lambda: require_asset_tier({"tier": "reviewed"}), refuses=False)
LOOP_ID = "123e4567-e89b-42d3-a456-426614174000"
check("replacement rejects no tombstone", lambda: require_supersession("old.html", None, LOOP_ID), refuses=True)
check("replacement rejects no loop receipt", lambda: require_supersession(
    "old.html", "_TO_DELETE/old.html", None), refuses=True)
check("replacement rejects an arbitrary loop string", lambda: require_supersession(
    "old.html", "_TO_DELETE/old.html", "loop:1"), refuses=True)
check("replacement accepts tombstone and loop receipt", lambda: require_supersession(
    "old.html", "_TO_DELETE/old.html", LOOP_ID), refuses=False)

with tempfile.TemporaryDirectory() as tmp:
    live = Path(tmp, "asset.html")
    tomb = Path(tmp, "_TO_DELETE", "asset.html")
    live.write_text("old")
    write_artifact_atomically(str(live), "new", tombstone_path=str(tomb), loop_ref=LOOP_ID)
    if live.read_text() == "new" and tomb.read_text() == "old":
        passes += 1
    else:
        fails.append("atomic supersession retains old tombstone and installs new")

with tempfile.TemporaryDirectory() as tmp:
    live = Path(tmp, "asset.html")
    tomb = Path(tmp, "_TO_DELETE", "asset.html")
    live.write_text("old")
    real_replace = os.replace

    def fail_final(source: str, target: str) -> None:
        if source.startswith(str(live) + ".tmp-") and target == str(live):
            raise OSError("fixture final install failure")
        real_replace(source, target)

    try:
        with mock.patch("lib.client_asset_controls.os.replace", side_effect=fail_final):
            write_artifact_atomically(str(live), "new", tombstone_path=str(tomb), loop_ref=LOOP_ID)
    except AssetControlRefusal:
        pass
    if live.read_text() == "old" and not tomb.exists():
        passes += 1
    else:
        fails.append("failed atomic install restores prior live artifact")
check("weekly batch rejects quote tweet", lambda: reject_weekly_quote_tweets(
    [{"content_type": "quote_tweet"}]), refuses=True)
check("weekly batch accepts non-reply draft", lambda: reject_weekly_quote_tweets(
    [{"content_type": "market_commentary"}]), refuses=False)

# The actual search-packet render entry point performs its control checks before
# touching fonts/photos, so malformed asset metadata reliably proves its refusal
# even on a CI host without the CARR vault mounted.  The renderer's legacy brand
# assets are now recovery-only, so these fixtures cross that boundary explicitly
# and still prove the controls fire before any asset read.
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, "properties.json").write_text(json.dumps({
        "client": {"findings": [], "confirmations": [["source", "confirmed"]]},
        "properties": [], "asset": {"tier": "reviewed"},
    }))
    render = subprocess.run([sys.executable, str(REPO / "pipelines" / "build-space-search.py"), tmp,
                             "--recovery", "--reason", "asset-control fixture", "--vault", tmp],
                            capture_output=True, text=True)
    if render.returncode == 2 and "client.findings" in render.stderr:
        passes += 1
    else:
        fails.append("search renderer refuses empty findings before render")
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, "properties.json").write_text(json.dumps({
        "client": {"findings": [["source", "finding"]],
                   "confirmations": [["source", "confirmed"]]},
        "properties": [], "asset": {"tier": "reviewed"},
    }))
    render = subprocess.run([sys.executable, str(REPO / "pipelines" / "build-space-search.py"), tmp,
                             "--recovery", "--reason", "asset-control fixture", "--vault", tmp],
                            capture_output=True, text=True)
    if render.returncode == 2 and "declined_and_why" in render.stderr:
        passes += 1
    else:
        fails.append("search renderer refuses missing declined rationale")
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, "properties.json").write_text(json.dumps({
        "client": {"findings": [["source", "finding"]],
                   "confirmations": [["source", "confirmed"]],
                   "declined_and_why": [{"option": "A", "why": "wrong size"}]},
        "properties": [], "asset": {},
    }))
    render = subprocess.run([sys.executable, str(REPO / "pipelines" / "build-space-search.py"), tmp,
                             "--recovery", "--reason", "asset-control fixture", "--vault", tmp],
                            capture_output=True, text=True)
    if render.returncode == 2 and "tier" in render.stderr:
        passes += 1
    else:
        fails.append("search renderer refuses untiered asset")

print(f"client-asset controls selftest — {passes}/{passes + len(fails)} passed")
if fails:
    print("FAILED: " + "; ".join(fails))
    raise SystemExit(1)
