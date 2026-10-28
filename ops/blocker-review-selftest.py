#!/usr/bin/env python3
"""blocker-review-selftest.py — fixtures for ops/blocker-review.py.

WHY THIS EXISTS. blocker-review.py is the tool the loop-drain task is told to
use — "already exists and reports what has genuinely unblocked; use it, do not
rebuild it." On 2026-08-21 it did not report anything at all: it died with
`psycopg.errors.InsufficientPrivilege: permission denied for table activity`
before printing a single line. The counterparty test joins four base tables
(activity, activity_kind, party, and the client/lead/vendor union) and the
credential it runs under — app_exporter_local — can read NONE of them. Measured
the same day: has_table_privilege says loop_item=True, activity=False,
activity_kind=False, party=False, client=False, lead=False, vendor=False. The
exporter is deliberately view-scoped (79 of 79 views readable, zero base
tables), so that branch could never have run under it.

A crash is the loud failure. The quiet one is worse and is also fixed here: the
module docstring promised FOUR clearable classes and the code implemented three,
with `capability` falling through to a bare `unknown += 1`. That is the same
shape as the gate-edit-gate defect of 2026-08-10 — "a docstring asserted the
coverage, so the gap read as closed."

These tests are pure: no database, no network. That is the point — the classifier
logic is the part that was wrong, and it should be provable without the
credential whose absence started this.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "blocker_review", os.path.join(REPO, "ops", "blocker-review.py"))
# The hyphen in the filename is why this is loaded by path rather than imported.
# Both of these can be None by signature, and a bare failure here would read as
# "the tests passed" to anything counting exit codes, so say which half is missing.
assert _spec is not None, "cannot load ops/blocker-review.py — file moved?"
assert _spec.loader is not None, "ops/blocker-review.py has no loader"
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

FAILS: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(fail="  FAIL  {label}  {detail}", detail=None, failures=FAILS)


def main() -> int:
    print("blocker-review-selftest")

    # ---- external_event: a date that has passed is no longer a wait ----
    today = date(2026, 8, 21)
    check("external_event clears on a past date",
          br.test_external_event(date(2026, 8, 20), today) is not None)
    check("external_event holds on a future date",
          br.test_external_event(date(2026, 8, 27), today) is None)
    check("external_event holds with no date at all",
          br.test_external_event(None, today) is None)
    check("external_event clears on exactly today",
          br.test_external_event(today, today) is not None)

    # ---- other_lane: cleared only when EVERY loop it names is closed ----
    check("other_lane clears when the loop it waits on is closed",
          br.test_other_lane("blocked on loop #123", "400",
                             [("123", "done")]) is not None)
    check("other_lane holds when one of two is still open",
          br.test_other_lane("waits on #123 and #124", "400",
                             [("123", "done"), ("124", "open")]) is None)
    check("other_lane holds when it names no loop",
          br.test_other_lane("a sysdev lane picks this up", "400", []) is None)
    check("other_lane ignores a self-reference",
          br.refs_in("this is loop #400 itself", "400") == set())

    # ---- capability: the catalog and the credential are ground truth ----
    exists = {"v_vendor_needs_type"}
    have_key = {"NEON_API_KEY"}
    rel = lambda n: n in exists
    cred = lambda n: n in have_key

    check("capability clears when the named view now exists",
          br.test_capability("needs the `v_vendor_needs_type` view", rel, cred) is not None)
    check("capability holds when the named view still does not exist",
          br.test_capability("needs the `v_not_built_yet` view", rel, cred) is None)
    check("capability clears when the named credential is present",
          br.test_capability("waits on `NEON_API_KEY` being on the machine",
                             rel, cred) is not None)
    check("capability holds when the named credential is absent",
          br.test_capability("waits on `SALESFORCE_TOKEN`", rel, cred) is None)

    # THE FALSE-POSITIVE BAR. The module's own docstring: "a false 'unblocked'
    # costs more than a missed one." A capability row naming a VERB is not
    # testable from here and must NOT clear — the repo's tools.js is not proof of
    # what production runs, and on 2026-08-21 production sat 12 commits behind
    # main, so a verb present in the checkout was genuinely absent from the live
    # Worker. Read the verb count off /release, not off the source tree.
    check("capability does NOT clear on a verb name",
          br.test_capability("needs a `set-vendor-category` verb that does not exist",
                             rel, cred) is None)
    check("capability does NOT clear on prose naming no identifier",
          br.test_capability("blocked until someone builds the thing", rel, cred) is None)

    # ---- counterparty: degradation is REPORTED, never silent ----
    # The whole failure being fixed is that an unrunnable test took the process
    # down. It must now downgrade AND say so; a branch that quietly returns "no"
    # would turn a crash into a wrong all-clear, which is worse.
    check("counterparty is declared untestable without the base tables",
          br.COUNTERPARTY_TABLES and all(isinstance(t, str) for t in br.COUNTERPARTY_TABLES))
    report = br.render_degradation(["activity", "party"])
    check("degradation names the tables it cannot read",
          "activity" in report and "party" in report, report)
    check("degradation is not silent", report.strip() != "")

    print()
    if FAILS:
        print(f"blocker-review-selftest: {len(FAILS)} FAILED — {', '.join(FAILS)}")
        return 1
    print("blocker-review-selftest: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
