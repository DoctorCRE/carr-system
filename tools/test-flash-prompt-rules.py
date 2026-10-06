#!/usr/bin/env python3
"""Contract tests for tools/flash-prompt-rules.py, interactive Flash's per-message rules.

The installed hook (~/.claude-local/settings.json) runs this entry point by path and
exits 0 silently when the file is missing, so a deleted or broken entry point looks
exactly like "no rule binds". These drive the real entry point and the real selector;
only the semantic binding transport is injected.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "flash-prompt-rules.py")
FAILURES: list[str] = []


def check(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except AssertionError as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name}: {exc!r}")


assert os.path.isfile(ENTRY), "the installed interactive hook's entry point is missing"
spec = importlib.util.spec_from_file_location("flash_prompt_rules", ENTRY)
if spec is None or spec.loader is None:
    raise ImportError(ENTRY)
fpr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fpr)
LOG = os.path.join(tempfile.mkdtemp(), "flash-prompt-rules.jsonl")
setattr(fpr, "LOG", LOG)  # keep fixture rows out of the real out/ log


class FakeClient:
    @staticmethod
    def noul(instructions, true="", false=""):
        return {"instructions": instructions, "criteria": {"true": true, "false": false}}


def judge(bind_raises=None):
    seen = []

    def rank(*args, **kwargs):
        raise AssertionError("shortlisting must be deterministic")

    def ask(state, questions):
        seen.append(state["situation"])
        if bind_raises:
            raise bind_raises
        return {"model": "jev-1.13.0", "answers": {key: {"noul": 0.9} for key in questions}}

    return {"rank": rank, "ask": ask, "client": FakeClient(), "titles": {}}, seen


def run(prompt, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert fpr.main(json.dumps({"prompt": prompt}), **kwargs) == 0
    return buf.getvalue().strip()


def last_log():
    with open(LOG, encoding="utf-8") as fh:
        return json.loads(fh.read().splitlines()[-1])


def records_advice_without_authoritative_context():
    kwargs, seen = judge()
    assert run("compare the artifact against what it should be before and after", **kwargs) == ""
    assert "may read, edit, run tests and use git" in seen[0], seen
    row = last_log()
    assert row["rules"] == [] and "review required" in row.get("error", ""), row
    assert "rule suggestions:" in row["error"], row


def skips_slash_commands_and_short_messages():
    kwargs, seen = judge()
    assert run("/clear", **kwargs) == ""
    assert run("fix it", **kwargs) == ""
    assert seen == [], seen


def outage_is_logged_not_silent():
    kwargs, _ = judge(bind_raises=TimeoutError("jev timed out"))
    assert run("add a CI check that the nightly export is non-empty", **kwargs) == ""
    row = last_log()
    assert row["rules"] == [] and "unavailable" in row.get("error", ""), row


def bad_input_exits_zero():
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert fpr.main("not json") == 0
    assert buf.getvalue() == ""


check("records advice without authoritative context", records_advice_without_authoritative_context)
check("skips slash commands and short messages", skips_slash_commands_and_short_messages)
check("a judgment outage is logged, not silent", outage_is_logged_not_silent)
check("bad input exits 0 with no output", bad_input_exits_zero)

if FAILURES:
    print(f"flash-prompt-rules: {len(FAILURES)} FAILED")
    sys.exit(1)
print("flash-prompt-rules: every assertion held")
