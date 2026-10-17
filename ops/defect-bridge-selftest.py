#!/usr/bin/env python3
"""ops/defect-bridge-selftest.py — the defect bridge (WR-000269), hermetic.

A fake record layer stands behind the door: unfinished-work (defect and
work_request legs, open and library, paged, with the real coverage shape),
work-request-card, and report-problem with the real verb's limits and
idempotency (situation is NOT stored, exactly like the live verb). Nothing here
touches the network or `./run.sh call`.

What it proves:
  * a lone defect yields exactly one Work Request on the first run, none on the
    second; two defects of one class yield one request naming both;
  * a defect a live request already names (full id, or `defect <8 hex>`) yields
    none; a declined request naming it does not count;
  * the health row reads 0 when every defect is linked, N otherwise, and always
    prints its bound action;
  * a failing door (defect read, request search, card read, a short census)
    files nothing and says why; a refused filing is reported, exit 1;
  * --dry-run calls no write verb; the per-run cap defers the smallest classes;
    a class too big for one payload still produces a payload the verb accepts;
  * the library has no shebang and no __main__; the entry, the nightly step and
    the health-check row are wired.
"""
from __future__ import annotations

import io
import json
import re
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import defect_bridge as db  # noqa: E402
from lib.record_call import FAILED, OK, REFUSED, TRANSIENT, VerbResult  # noqa: E402

KEY = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
AC_ID = re.compile(r"^[A-Z][A-Z0-9-]{1,63}$")
REPORT_FIELDS = {"idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"}


class FakeStore:
    """The slice of the record layer the bridge reads and writes."""

    def __init__(self) -> None:
        self.defects: list[dict[str, Any]] = []
        self.wrs: dict[str, dict[str, Any]] = {}
        self.keys: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, str] = {}          # "verb" or "verb:kind" -> outcome kind
        self.short_count = False
        self.refuse_report = False
        self.next_ref = 900

    def defect(self, cls: str, claimed: str = "it worked", when: str = "2026-10-01") -> str:
        defect_id = str(uuid.uuid4())
        self.defects.append({"id": defect_id, "cls": cls, "claimed": claimed, "at": when})
        return defect_id

    def wr(self, title: str, outcome: str = "do it", criteria: tuple[str, ...] = ("done",),
           state: str = "captured") -> str:
        ref = f"WR-{self.next_ref:06d}"
        self.next_ref += 1
        self.wrs[ref] = {"title": title, "desired_outcome": outcome, "state": state,
                         "acceptance_criteria": [{"id": f"AC-{i + 1}", "text": t}
                                                 for i, t in enumerate(criteria)]}
        return ref

    def door(self, verb: str, sent: Mapping[str, Any]) -> VerbResult:
        args: dict[str, Any] = json.loads(json.dumps(sent))
        self.calls.append((verb, args))
        for key in (verb, f"{verb}:{args.get('kinds', '')}"):
            if key in self.fail:
                return VerbResult(verb, self.fail[key], detail="fake door says no")
        if verb == "unfinished-work":
            return VerbResult(verb, OK, reply=self._census(args))
        if verb == "work-request-card":
            wr = self.wrs.get(args["work_request"])
            if wr is None:
                return VerbResult(verb, REFUSED, reply={"ok": False, "error": "not_found"})
            return VerbResult(verb, OK, reply={"ok": True, "human_ref": args["work_request"], **wr})
        if verb == "report-problem":
            return self._report(args)
        return VerbResult(verb, REFUSED, reply={"ok": False, "error": "unknown_tool"})

    def _census(self, args: dict[str, Any]) -> dict[str, Any]:
        kind, live = args["kinds"], bool(args.get("live_library"))
        if kind == "defect":
            rows = [] if live else [
                {"id": d["id"], "kind": "defect", "title": f"{d['cls']}: {d['claimed']}",
                 "opened_at": d["at"] + "T00:00:00.000Z", "last_activity_at": d["at"] + "T12:00:00.000Z",
                 "identity": {"defect_id": d["id"]}, "completed": False, "cancelled": False}
                for d in self.defects]
            cover: dict[str, Any] = {"kind": "defect", "state": "partial",
                                     "reason": "fix_status_not_recorded"}
        else:
            text = str(args.get("text") or "").lower()
            rows = []
            for ref, wr in sorted(self.wrs.items()):
                completed = wr["state"] in ("confirmed_closed", "declined", "superseded")
                if completed != live or text not in wr["title"].lower():
                    continue
                rows.append({"id": ref, "kind": "work_request", "title": wr["title"],
                             "state": wr["state"], "completed": completed,
                             "cancelled": wr["state"] in ("declined", "superseded")})
            cover = {"kind": "work_request", "state": "complete", "reason": None}
        start = int(args.get("cursor") or 0)
        page = rows[start:start + int(args["limit"])]
        more = start + len(page) < len(rows)
        total = len(rows) + (1 if self.short_count and kind == "defect" else 0)
        return {"ok": True, "items": page, "census_complete": cover["state"] == "complete",
                "coverage": [{**cover, "source_ref": "x", "count_total": total,
                              "count_returned": len(page)}],
                "next_cursor": str(start + len(page)) if more else None}

    def _report(self, args: dict[str, Any]) -> VerbResult:
        problems = []
        if set(args) != REPORT_FIELDS:
            problems.append(f"fields {sorted(set(args) ^ REPORT_FIELDS)}")
        if not KEY.match(str(args.get("idempotency_key", ""))):
            problems.append("idempotency_key")
        for name, limit in (("title", 200), ("situation", 1000), ("desired_outcome", 2000)):
            if not 1 <= len(str(args.get(name, ""))) <= limit:
                problems.append(f"{name} length {len(str(args.get(name, '')))}")
        criteria = args.get("acceptance_criteria") or []
        if not 1 <= len(criteria) <= 12:
            problems.append("acceptance_criteria count")
        for ac in criteria:
            if set(ac) != {"id", "text"} or not AC_ID.match(ac["id"]) or not 1 <= len(ac["text"]) <= 500:
                problems.append(f"criterion {ac}")
        refusal = ({"ok": False, "error": "invalid", "problems": problems} if problems else
                   {"ok": False, "error": "current_situation_source_not_found"} if self.refuse_report
                   else None)
        if refusal:   # the real door's detail is the refusal payload (lib/record_call.py)
            return VerbResult("report-problem", REFUSED, reply=refusal, detail=json.dumps(refusal))
        key = args["idempotency_key"]
        if key not in self.keys:   # the real envelope replays a key; situation is not stored
            self.keys[key] = self.wr(args["title"], args["desired_outcome"],
                                     tuple(ac["text"] for ac in args["acceptance_criteria"]))
        return VerbResult("report-problem", OK, reply={"ok": True, "human_ref": self.keys[key],
                                                       "state": "captured"})

    def writes(self) -> list[dict[str, Any]]:
        return [a for v, a in self.calls if v == "report-problem"]


def run(store: FakeStore, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = db.main(list(argv), door=store.door)
    return rc, out.getvalue()


class Filing(unittest.TestCase):
    def test_lone_defect_files_once_then_never_again(self) -> None:
        store = FakeStore()
        only = store.defect("stale-read")
        rc, out = run(store)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(store.writes()), 1)
        self.assertEqual(len(store.wrs), 1)
        wr = next(iter(store.wrs.values()))
        self.assertEqual(wr["title"], "Defect class stale-read: 1 unaddressed")
        self.assertIn(f"defect:{only}", wr["desired_outcome"])
        self.assertIn(db.PRODUCT_TASK, store.writes()[0]["situation"])
        self.assertIn(only, store.writes()[0]["situation"])
        self.assertIn("OK unaddressed defects", out)
        rc, out = run(store)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(store.writes()), 1, "the second run filed again")
        self.assertIn("nothing to file", out)

    def test_two_defects_of_one_class_make_one_request(self) -> None:
        store = FakeStore()
        a = store.defect("wrong-signal", "first", "2026-10-01")
        b = store.defect("wrong-signal", "second, newest", "2026-10-05")
        run(store)
        self.assertEqual(len(store.writes()), 1)
        sent = store.writes()[0]
        self.assertEqual(sent["title"], "Defect class wrong-signal: 2 unaddressed")
        self.assertIn(a, sent["desired_outcome"])
        self.assertIn(b, sent["desired_outcome"])
        self.assertIn('claimed "second, newest"', sent["situation"])

    def test_two_classes_make_two_requests(self) -> None:
        store = FakeStore()
        store.defect("one")
        store.defect("two")
        run(store)
        self.assertEqual(sorted(w["title"] for w in store.writes()),
                         ["Defect class one: 1 unaddressed", "Defect class two: 1 unaddressed"])

    def test_a_linked_defect_files_nothing(self) -> None:
        store = FakeStore()
        short = store.defect("named-short")
        full = store.defect("named-full")
        store.wr(f"Fix the lock (defect {short[:8]}; unblocks the board)")
        store.wr("Repair the defect sweep", criteria=(f"defect:{full} is fixed",),
                 state="confirmed_closed")
        rc, out = run(store)
        self.assertEqual((rc, store.writes()), (0, []), out)
        self.assertIn("2 linked", out)

    def test_a_declined_request_does_not_link(self) -> None:
        store = FakeStore()
        d = store.defect("declined-once")
        store.wr(f"Old defect {d[:8]} request", state="declined")
        run(store)
        self.assertEqual(len(store.writes()), 1)

    def test_new_defect_in_a_filed_class_is_a_new_open_set(self) -> None:
        store = FakeStore()
        store.defect("recurs")
        run(store)
        late = store.defect("recurs", when="2026-10-09")
        run(store)
        self.assertEqual(len(store.writes()), 2)
        self.assertEqual(store.writes()[1]["title"], "Defect class recurs: 1 unaddressed")
        self.assertIn(late, store.writes()[1]["desired_outcome"])
        self.assertNotEqual(store.writes()[0]["idempotency_key"], store.writes()[1]["idempotency_key"])

    def test_cap_defers_the_smallest_classes(self) -> None:
        store = FakeStore()
        for _ in range(3):
            store.defect("big")
        store.defect("small-old", when="2026-09-01")
        store.defect("small-new", when="2026-10-09")
        rc, out = run(store, "--max-filings", "2")
        self.assertEqual([w["title"] for w in store.writes()],
                         ["Defect class big: 3 unaddressed", "Defect class small-new: 1 unaddressed"])
        self.assertIn("1 more class(es)", out)
        run(store, "--max-filings", "2")
        self.assertEqual(len(store.writes()), 3)

    def test_an_oversized_class_still_fits_the_verb(self) -> None:
        # 150 ids outgrow one request (desired outcome plus ten overflow
        # criteria); the verb still accepts it, every id it carries reads back
        # as linked, and the rest are the next run's open set.
        store = FakeStore()
        ids = [store.defect("huge", f"claim {i} " + "x" * 400) for i in range(150)]
        rc, out = run(store)
        self.assertEqual(rc, 0, out)
        first = store.writes()[0]
        carried = db.named_defects(json.dumps(first), ids)
        self.assertGreater(len(carried), 100)
        self.assertLess(len(carried), 150)
        self.assertEqual(first["title"], f"Defect class huge: {len(carried)} unaddressed")
        self.assertEqual(len(first["acceptance_criteria"]), 12)
        rc, out = run(store)
        self.assertEqual(rc, 0, out)
        second = store.writes()[1]
        self.assertEqual(second["title"], f"Defect class huge: {150 - len(carried)} unaddressed")
        self.assertEqual(carried | db.named_defects(json.dumps(second), ids), set(ids))
        run(store)
        self.assertEqual(len(store.writes()), 2, "a carried id was not read back as linked")

    def test_paging_reads_every_row(self) -> None:
        store = FakeStore()
        for i in range(5):
            store.defect(f"paged-{i}")
        old = db.PAGE
        db.PAGE = 2
        try:
            rc, out = run(store, "--dry-run")
        finally:
            db.PAGE = old
        self.assertEqual(rc, 0, out)
        self.assertIn("5 recorded", out)


class Failures(unittest.TestCase):
    def assert_filed_nothing(self, store: FakeStore, why: str) -> None:
        rc, out = run(store)
        self.assertEqual(rc, 1, out)
        self.assertEqual(store.writes(), [])
        self.assertIn("FILED NOTHING", out)
        self.assertIn(why, out)

    def test_defect_read_failing(self) -> None:
        store = FakeStore()
        store.defect("x")
        store.fail["unfinished-work:defect"] = TRANSIENT
        self.assert_filed_nothing(store, "unfinished-work (defect) unfinished-work transient")

    def test_request_search_failing(self) -> None:
        store = FakeStore()
        store.defect("x")
        store.fail["unfinished-work:work_request"] = FAILED
        self.assert_filed_nothing(store, "unfinished-work (work_request)")

    def test_card_read_failing(self) -> None:
        store = FakeStore()
        store.defect("x")
        store.wr("an unrelated defect request")
        store.fail["work-request-card"] = REFUSED
        self.assert_filed_nothing(store, "work-request-card WR-000900")

    def test_short_census(self) -> None:
        store = FakeStore()
        store.defect("x")
        store.short_count = True
        self.assert_filed_nothing(store, "read 1 rows of 2 counted")

    def test_refused_filing_is_reported(self) -> None:
        store = FakeStore()
        store.defect("x")
        store.refuse_report = True
        rc, out = run(store)
        self.assertEqual(rc, 1)
        self.assertIn("NOT FILED Defect class x: 1 unaddressed", out)
        self.assertIn("current_situation_source_not_found", out)
        self.assertIn("⚠︎ unaddressed defects  1 of 1", out)


class Surfaces(unittest.TestCase):
    def test_health_row_reads_zero_when_everything_is_linked(self) -> None:
        store = FakeStore()
        d = store.defect("done")
        store.wr(f"Fix defect {d[:8]}")
        rc, out = run(store, "--health-row")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("  OK unaddressed defects  0 of 1 "), out)
        for part in ("on breach:", "report-problem", "owner claude", "verify:",
                     "auto-clears when 0"):
            self.assertIn(part, out)
        self.assertEqual(store.writes(), [])

    def test_health_row_counts_the_unaddressed(self) -> None:
        store = FakeStore()
        store.defect("a")
        store.defect("a")
        store.defect("b")
        rc, out = run(store, "--health-row")
        self.assertEqual(rc, 1)
        self.assertIn("⚠︎ unaddressed defects  3 of 3 recorded defects in 2 class(es)", out)
        self.assertIn("on breach:", out)

    def test_health_row_never_reads_clear_on_a_failed_read(self) -> None:
        store = FakeStore()
        store.fail["unfinished-work"] = TRANSIENT
        rc, out = run(store, "--health-row")
        self.assertEqual(rc, 1)
        self.assertIn("UNKNOWN", out)
        self.assertIn("on breach:", out)

    def test_dry_run_writes_nothing(self) -> None:
        store = FakeStore()
        d = store.defect("would-be")
        rc, out = run(store, "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(store.writes(), [])
        self.assertIn("would file: Defect class would-be: 1 unaddressed", out)
        self.assertIn(d[:8], out)
        self.assertEqual({v for v, _ in store.calls}, {"unfinished-work"})


class Wiring(unittest.TestCase):
    def test_library_is_a_library(self) -> None:
        src = (ROOT / "lib/defect_bridge.py").read_text()
        self.assertFalse(src.startswith("#!"))
        self.assertNotIn("__main__", src)

    def test_entry_nightly_and_health_row_are_wired(self) -> None:
        entry = (ROOT / "ops/defect-bridge.py").read_text()
        self.assertIn("from lib.defect_bridge import main", entry)
        nightly = (ROOT / "bin/nightly.sh").read_text()
        grok = nightly.index('step "Grok authentication health"')
        bridge = nightly.index('step "defect bridge"')
        self.assertLess(grok, bridge)
        self.assertIn("./.venv/bin/python ops/defect-bridge.py\n", nightly[bridge:bridge + 200])
        health = (ROOT / "tools/health-check.py").read_text()
        self.assertIn('"defect-bridge.py"), "--health-row"]', health)


if __name__ == "__main__":
    unittest.main(verbosity=1)
