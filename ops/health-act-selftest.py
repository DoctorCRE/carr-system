"""health-act-selftest.py — hermetic proof of tools/health_breach.py.

`./run.sh health --act` executes every red health row's bound action: one
deduplicated record-layer loop per row, auto-cleared when the row goes green.
This suite drives the shipped library against a FAKE verb door (an in-memory
loop table that answers loop-board / read-loop / add-loop / update-loop /
close-loop the way the record layer does) and proves:

  * a red row with a bound action and no loop gets exactly one loop;
  * a second run files nothing new (dedup lives in the record layer);
  * a row whose own reader already filed its loop is left alone ("covered");
  * a changed bound action updates the same loop instead of filing another;
  * a row with no bound action is counted, never filed;
  * a green row's loop is closed when the system owns it, noted-cleared when a
    person owns it, and nothing clears on a partial run;
  * a failed dedup search refuses to file (fail closed);
  * the audit counts red rows with no open loop, and duplicates;
  * the parser reads owner/remediation/verify/auto-clear from real row text.

THE ASSERTIONS ARE PROVEN TO BITE: the same scenario runs against mutants of
the library (one that cannot see existing loops, so it files twice; one that
drops a red row; one that never auto-clears) and each mutant must fail.

No network, no record layer, no repository writes: receipts go to a temp dir.
"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.record_call import VerbResult  # noqa: E402

LIB_PATH = REPO / "tools" / "health_breach.py"
NOW = lambda: datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)  # noqa: E731


def load(name="health_breach_under_test"):
    spec = importlib.util.spec_from_file_location(name, LIB_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


# Row texts lifted from the 2026-10-12 canonical `./run.sh health` run.
SEAT = ("FAIL grok latency=3.151s layer=auth · on breach: open/update one deduplicated loop "
        "seat-health:grok; owner orchestrator; Restore grok login on Studio. Verify: this seat must "
        "return exactly 323 for 17 * 19; auto-clear on the next passing daily probe.")
JANITOR = ("WARN branch janitor: scheduled evidence absent or stale · on breach: owner orchestrator · "
           "run hooks/worktree-self-plumb.py --reap --fleet · verify out/orch/branch-janitor-report.json · "
           "auto-clear after a complete scheduled run")
GROK = ("FAIL Grok session — credential timestamps unavailable or invalid · on breach: one incident loop; "
        "one alert attempt/channel/UTC day · owner Joe · remediation run grok login · verify grok models "
        "succeeds · auto-clear after fresh successful authentication under the incident lock · incident action FAILED")
JOBS = ("WARN scheduled jobs com.carr.calendar-eventkit failing_exit: last exit status 1 · on breach: "
        "tools/job-watchdog.py scan files/updates loop scheduled_jobs:com.carr.calendar-eventkit:failing_exit · "
        "owner orchestrator · fix: inspect the job log and repair the failing command; verify a successful run · "
        "verify: python3 ops/scheduled-jobs-check.py · auto-clear: next complete scan without failing_exit for "
        "com.carr.calendar-eventkit")
PREBRIEF = ("calendar-prebrief-projection-joe-daily MISSED its 2026-10-12 06:30 CDT run (never scheduled); "
            "2 of the last 2 weekday slot(s) missed · on breach: update loop #665 (owner joe-desk session) — "
            "read out/calendar-prebrief-joe-launchd.log and the job's failure_class, fix the named gate, verify "
            "with the next 06:30 weekday receipt; clears when the latest due slot succeeds with event_count > 0")
UPTIME = ("WARN production uptime · monitor unreachable or response invalid · on breach: carr-uptime replays "
          "one production incident loop; this reader opens one monitor-availability loop if monitoring fails; "
          "owner orchestrator; fix: restore failed routes or configure/restart carr-uptime and its secrets; "
          "verify: three JSON probes, Healthchecks down/up delivery, and incident loop reconciliation; "
          "auto-clear: fresh passing monitor, no pending alerts or incident loops")
JEV = ("no usable call recorded; recent attempt failed (state_unreadable); loop error · on breach: open/update "
       "one deduplicated loop (owner Joe; repair the Jev outage state file); verify a usable Jev judgment; "
       "auto-clear on that judgment")
TAILSCALE = ("FAIL tailscale stopped · on breach: open/update one dedup loop 'Tailscale down on this Mac' · "
             "owner orchestrator · fix: launchctl kickstart gui/$(id -u)/com.carr.tailscale-up · verify: "
             "Tailscale status --json reports Running · auto-clear: the next health run reads a running node")


class FakeDoor:
    """In-memory record layer: the loop verbs this consumer uses, with the
    record layer's own semantics (ilike search over title+body, open-only
    board, base_version guard, closed loops leave the board)."""

    def __init__(self, loops=(), fail_search=False):
        self.loops = {}
        self.calls = []
        self.fail_search = fail_search
        for loop in loops:
            self.seed(**loop)

    def seed(self, number, body, owner="claude", status="open", version=1):
        self.loops[str(number)] = {"number": str(number), "body": body, "owner": owner,
                                   "status": status, "version": version, "kind": "open_loop"}

    def writes(self, verb=None):
        return [c for c in self.calls if c[0] in ("add-loop", "update-loop", "close-loop")
                and (verb is None or c[0] == verb)]

    def open_loops(self):
        return [l for l in self.loops.values() if l["status"] == "open"]

    @staticmethod
    def _ilike(pattern, text):
        regex = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
        return re.fullmatch(regex, text, re.I | re.S) is not None

    def __call__(self, verb, args):
        self.calls.append((verb, dict(args)))
        if verb == "loop-board":
            if self.fail_search:
                return VerbResult(verb, "transient", detail="could not reach the deployed Worker")
            rows = [l for l in self.open_loops()
                    if self._ilike("%" + args.get("search", "") + "%", l["body"])]
            rows = sorted(rows, key=lambda l: int(l["number"]))[: int(args.get("limit", 60))]
            return VerbResult(verb, "ok", reply={"count": len(rows), "loops": [
                {"number": l["number"], "owner": l["owner"], "version": l["version"],
                 "label": l["body"].split("\n", 1)[0]} for l in rows]})
        if verb == "read-loop":
            loop = self.loops.get(str(args.get("number")))
            if not loop:
                return VerbResult(verb, "refused", reply={"error": "not_found"})
            return VerbResult(verb, "ok", reply={"loop": dict(loop), "amended": False, "amendments": []})
        if verb == "add-loop":
            for field in ("idempotency_key", "kind", "owner", "blocker", "blocker_detail", "body"):
                if not args.get(field):
                    return VerbResult(verb, "refused", reply={"error": f"missing_{field}"})
            number = str(800 + len(self.loops))
            self.seed(number, args["body"], owner=args["owner"])
            return VerbResult(verb, "ok", reply={"ok": True, "loop_id": "id-" + number, "number": number})
        loop = self.loops.get(str(args.get("number")))
        if not loop or loop["status"] != "open":
            return VerbResult(verb, "refused", reply={"error": "loop_not_open"})
        if args.get("base_version") != loop["version"]:
            return VerbResult(verb, "refused", reply={"error": "version_conflict"})
        if verb == "update-loop":
            loop["body"] = args["body"]
        elif verb == "close-loop":
            if not args.get("outcome"):
                return VerbResult(verb, "refused", reply={"error": "outcome_required"})
            loop["status"] = args.get("resolution", "done")
        loop["version"] += 1
        return VerbResult(verb, "ok", reply={"ok": True})


def _load_tool_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finding(key, detail, subject=""):
    return {"key": key, "subject": subject, "detail": detail, "count": 1,
            "hard_error": False, "time_rolling": False}


RED = [
    finding("branch_janitor", JANITOR, "three-repo-retirement"),   # bound, no loop yet -> filed
    finding("ai_seat_health", SEAT, "grok"),                       # reader-filed loop -> covered
    finding("export_receipt", "STALE vendors.xlsx (last ok 2026-10-09T07:13)", "vendors.xlsx"),  # unbound
    finding("calendar_prebrief_missed_run", PREBRIEF, "calendar-prebrief-projection-joe-daily"),  # #665
    finding("grok_session", GROK),                                 # Joe-owned, no loop -> filed as joe
    finding("scheduled_jobs_failing_exit", JOBS, "com.carr.calendar-eventkit"),  # names its filer -> delegated
]


def world(lib):
    """Prior state: a reader-filed seat loop (twice: a real duplicate), loop
    #665, and two loops this consumer filed for rows that are now green."""
    green_claude = lib.breach_from_finding(finding("tailscale", TAILSCALE, "local-node"))
    green_joe = lib.breach_from_finding(finding("jev_live_outage", JEV))
    return FakeDoor([
        {"number": 713, "body": SEAT.split(" · ", 1)[1]},
        {"number": 738, "body": SEAT.split(" · ", 1)[1]},
        {"number": 665, "body": "2026-09-27: helper INSTALLED on the Studio", "owner": "joe"},
        {"number": 790, "body": f"{green_claude.tag}{green_claude.digest}] FAIL tailscale stopped\nrest"},
        {"number": 791, "body": f"{green_joe.tag}{green_joe.digest}] WARN jev\nrest", "owner": "joe"},
    ])


def scenario(lib):
    """Run the act/audit story against a library; return the list of failures."""
    failures = []
    def expect(ok, message):
        if not ok:
            failures.append(message)
    with tempfile.TemporaryDirectory() as tmp:
        receipts = Path(tmp) / "health-act.jsonl"
        door = world(lib)

        before = lib.audit(RED, door)
        jobs_row = "scheduled_jobs_failing_exit/com.carr.calendar-eventkit"
        expect(sorted(before.unlooped) == sorted(["branch_janitor/three-repo-retirement",
                                                  "export_receipt/vendors.xlsx", "grok_session", jobs_row]),
               f"audit before act: unlooped {before.unlooped}")
        expect(before.delegated == [(jobs_row, "tools/job-watchdog.py")], f"delegated {before.delegated}")
        expect(before.unbound == ["export_receipt/vendors.xlsx"], f"audit unbound {before.unbound}")
        expect([r for r, _ in before.duplicated] == ["ai_seat_health/grok"],
               f"audit duplicates {before.duplicated}")

        report = lib.act(RED, door, receipts_path=receipts, complete=True, now=NOW)
        decisions = {r["row_id"]: r["decision"] for r in report.receipts}
        adds = door.writes("add-loop")
        expect(len(adds) == 2, f"first run filed {len(adds)} loop(s), want 2")
        expect(decisions.get("branch_janitor/three-repo-retirement") == "filed", f"janitor: {decisions}")
        expect(decisions.get("grok_session") == "filed", f"grok: {decisions}")
        grok_add = [a for _, a in adds if "grok_session" in a["body"]]
        expect(bool(grok_add) and grok_add[0]["owner"] == "joe" and grok_add[0]["blocker"] == "human_only",
               "a row owned by Joe must file a loop owned by joe with a human_only blocker")
        janitor_add = [a for _, a in adds if "branch_janitor" in a["body"]]
        expect(bool(janitor_add) and "Remediation: run hooks/worktree-self-plumb.py --reap --fleet"
               in janitor_add[0]["body"] and "Verify: out/orch/branch-janitor-report.json" in janitor_add[0]["body"],
               "the filed loop must carry the row's remediation and verify text")
        expect(decisions.get("ai_seat_health/grok") == "covered", "reader-filed seat loop must be covered")
        seat = [r for r in report.receipts if r["row_id"] == "ai_seat_health/grok"]
        expect(bool(seat) and seat[0].get("duplicates") == ["713", "738"], "duplicates must be receipted")
        expect(decisions.get("calendar_prebrief_missed_run/calendar-prebrief-projection-joe-daily") == "covered",
               "a row naming loop #665 is covered by #665 while it is open")
        expect(decisions.get("export_receipt/vendors.xlsx") == "unbound", "an unbound row is never filed")
        expect(decisions.get(jobs_row) == "delegated" and not [a for _, a in adds if "eventkit" in a["body"]],
               "a row that names its own filer is never filed by the consumer")
        expect(door.loops["790"]["status"] == "done", "a green row's claude-owned loop must close")
        expect(door.loops["791"]["status"] == "open" and "CLEARED" in door.loops["791"]["body"],
               "a green row's joe-owned loop is noted cleared and left open for the owner")
        lines = receipts.read_text().splitlines()
        expect(len(lines) == len(report.receipts) and all(json.loads(l)["decision"] for l in lines),
               "one receipt line per action")

        after = lib.audit(RED, door)
        expect(sorted(after.unlooped) == sorted(["export_receipt/vendors.xlsx", jobs_row]),
               f"audit after act: {after.unlooped}")

        writes_before = len(door.writes())
        again = lib.act(RED, door, receipts_path=receipts, complete=True, now=NOW)
        expect(len(door.writes()) == writes_before, "a second run with the same rows must write nothing")
        expect(len([l for l in door.open_loops() if "branch_janitor" in l["body"]]) == 1,
               "exactly one open loop for the janitor row after two runs")
        expect(again.count("unchanged") == 2, f"second run: {again.summary()}")

        changed = [dict(f) for f in RED]
        changed[0]["detail"] = JANITOR.replace("--reap --fleet", "--reap --fleet --verbose")
        third = lib.act(changed, door, receipts_path=receipts, complete=True, now=NOW)
        expect(third.count("updated") == 1 and len(door.writes("add-loop")) == 2,
               "a changed bound action updates the same loop, never files another")

        partial = FakeDoor()
        partial.seed(900, f"{lib.TAG}tailscale/local-node 00000000] FAIL\nrest")
        lib.act([], partial, receipts_path=receipts, complete=False, now=NOW)
        expect(partial.loops["900"]["status"] == "open", "a partial run must clear nothing")

        broken = FakeDoor(fail_search=True)
        refused = lib.act(RED[:1], broken, receipts_path=receipts, complete=True, now=NOW)
        expect(not broken.writes() and refused.errors >= 1, "a failed dedup search must refuse to file")
        expect(lib.render_audit(lib.audit(RED, broken)).lstrip().startswith("UNKNOWN"),
               "an unreadable record layer prints UNKNOWN, never a count")
    return failures


class HealthActTests(unittest.TestCase):
    def test_shipped_library_passes_the_scenario(self):
        self.assertEqual(scenario(load()), [])

    def test_mutant_that_cannot_see_existing_loops_files_twice_and_fails(self):
        lib = load("mutant_blind")
        lib.find_open_loops = lambda breaches, door, workers=8: {b.row_id: ([], None) for b in breaches}
        self.assertTrue(any("second run" in f or "exactly one open loop" in f for f in scenario(lib)))

    def test_mutant_that_skips_a_red_row_fails(self):
        lib = load("mutant_skip")
        real = lib.breach_from_finding
        def drop_janitor(f):
            b = real(f)
            return b.__class__(**{**b.__dict__, "action": None}) if f["key"] == "branch_janitor" else b
        lib.breach_from_finding = drop_janitor
        self.assertTrue(any("janitor" in f for f in scenario(lib)))

    def test_mutant_that_never_auto_clears_fails(self):
        lib = load("mutant_noclear")
        lib.OWN_LOOPS_LIMIT = 0
        self.assertTrue(any("must close" in f for f in scenario(lib)))

    # ── auto-clear needs green evidence (review of PR #1714, HIGH) ──────────
    # A night when seat state could not be read emits ONE bare `ai_seat_health`
    # row. Every `ai_seat_health/<seat>` loop is then "not red", but nothing
    # proved any seat green, so none of them may close.

    UNREADABLE = ("Seat health evidence unreadable; on breach: orchestrator repairs ops/seat-health.py "
                  "and reruns the daily exact-value probes; auto-clear when all seats pass")

    def own_loops(self, lib, *rows):
        door = FakeDoor()
        for number, (key, subject) in enumerate(rows, start=950):
            b = lib.breach_from_finding(finding(key, SEAT if key == "ai_seat_health" else TAILSCALE, subject))
            door.seed(number, f"{b.tag}{b.digest}] FAIL {b.row_id}\nrest")
        return door

    def test_an_unevaluated_seat_family_closes_no_seat_loop(self):
        lib = load()
        door = self.own_loops(lib, ("ai_seat_health", "grok"), ("ai_seat_health", "codex"),
                              ("tailscale", "local-node"))
        with tempfile.TemporaryDirectory() as tmp:
            report = lib.act([finding("ai_seat_health", self.UNREADABLE)], door,
                             receipts_path=Path(tmp) / "r.jsonl", complete=True,
                             green={"ai_seat_health": None}, now=NOW)
        self.assertEqual(door.loops["950"]["status"], "open", "grok's loop closed on an unread night")
        self.assertEqual(door.loops["951"]["status"], "open", "codex's loop closed on an unread night")
        self.assertEqual(door.loops["952"]["status"], "done", "a green row in another family still clears")
        withheld = sorted(r["row_id"] for r in report.receipts if r["decision"] == "withheld")
        self.assertEqual(withheld, ["ai_seat_health/codex", "ai_seat_health/grok"])

    def test_a_seat_loop_clears_only_when_its_seat_reported_green(self):
        lib = load()
        door = self.own_loops(lib, ("ai_seat_health", "grok"), ("ai_seat_health", "retired-seat"),
                              ("ai_seat_health", ""))
        with tempfile.TemporaryDirectory() as tmp:
            lib.act([], door, receipts_path=Path(tmp) / "r.jsonl", complete=True,
                    green={"ai_seat_health": {"grok", "codex"}}, now=NOW)
        self.assertEqual(door.loops["950"]["status"], "done", "a seat reported PASS clears")
        self.assertEqual(door.loops["951"]["status"], "open", "a seat nobody evaluated never clears")
        self.assertEqual(door.loops["952"]["status"], "done",
                         "the bare unreadable-evidence row clears once the family was read")

    def test_a_red_bare_row_withholds_every_loop_of_its_family(self):
        """The same hole for any family whose evaluation failure emits a bare
        row (credential_health "check failed ..."): its per-subject loops are
        not proven green by that night's run."""
        lib = load()
        door = FakeDoor()
        b = lib.breach_from_finding(finding("credential_health", TAILSCALE, "github-app"))
        door.seed(960, f"{b.tag}{b.digest}] FAIL\nrest")
        with tempfile.TemporaryDirectory() as tmp:
            lib.act([finding("credential_health", "check failed (OSError: x)")], door,
                    receipts_path=Path(tmp) / "r.jsonl", complete=True, now=NOW)
        self.assertEqual(door.loops["960"]["status"], "open")

    def test_health_check_reports_no_seat_green_when_seat_rows_raise(self):
        """End to end through the shipped `_canonical_health` and act(): the
        seat reader raises, and no per-seat loop closes."""
        import contextlib, io
        findings_suite = _load_tool_module("health_check_findings_selftest",
                                           REPO / "tools" / "health-check-findings-selftest.py")
        lib = load()
        for rows, open_after in (
                (TypeError("fixture seat report"), {"950", "951"}),
                (["PASS grok latency=1s layer=none · ok", SEAT.replace("grok", "codex")], {"951"})):
            with self.subTest(rows=rows):
                ns = findings_suite.PaidCapCanonicalHealthTests().all_namespace()
                ns["_jev_paid_cap_row"] = lambda: "OK jev paid cap"
                def seat_rows(rows=rows):
                    if isinstance(rows, Exception):
                        raise rows
                    return rows
                ns["_seat_health_rows"] = seat_rows
                with contextlib.redirect_stdout(io.StringIO()):
                    ns["_canonical_health"]()
                door = self.own_loops(lib, ("ai_seat_health", "grok"), ("ai_seat_health", "codex"))
                with tempfile.TemporaryDirectory() as tmp:
                    lib.act(list(ns["_FINDINGS"]), door, receipts_path=Path(tmp) / "r.jsonl",
                            complete=True, green=dict(ns["_GREEN_EVIDENCE"]), now=NOW)
                self.assertEqual({n for n in ("950", "951") if door.loops[n]["status"] == "open"}, open_after)

    def test_the_consumer_passes_the_green_evidence(self):
        health = (REPO / "tools" / "health-check.py").read_text()
        self.assertTrue("complete=True, green=dict(_GREEN_EVIDENCE)" in health,
                        "_breach_consumer must hand act() the run's green evidence")

    def test_parser_reads_real_row_text(self):
        lib = load()
        cases = [
            ("ai_seat_health", SEAT, "orchestrator", "Restore grok login on Studio",
             "this seat must return exactly 323 for 17 * 19", "on the next passing daily probe",
             "seat-health:grok"),
            ("scheduled_jobs_failing_exit", JOBS, "orchestrator",
             "inspect the job log and repair the failing command; verify a successful run",
             "python3 ops/scheduled-jobs-check.py",
             "next complete scan without failing_exit for com.carr.calendar-eventkit",
             "scheduled_jobs:com.carr.calendar-eventkit:failing_exit"),
            ("grok_session", GROK, "Joe", "run grok login", "grok models succeeds",
             "after fresh successful authentication under the incident lock", "health-breach:grok_session"),
            ("production_uptime", UPTIME, "orchestrator",
             "restore failed routes or configure/restart carr-uptime and its secrets",
             "three JSON probes, Healthchecks down/up delivery, and incident loop reconciliation",
             "fresh passing monitor, no pending alerts or incident loops", "health-breach:production_uptime"),
            ("jev_live_outage", JEV, "Joe", "repair the Jev outage state file", "a usable Jev judgment",
             "on that judgment", "health-breach:jev_live_outage"),
            ("branch_janitor", JANITOR, "orchestrator", "run hooks/worktree-self-plumb.py --reap --fleet",
             "out/orch/branch-janitor-report.json", "after a complete scheduled run",
             "health-breach:branch_janitor"),
            ("tailscale", TAILSCALE, "orchestrator", "launchctl kickstart gui/$(id -u)/com.carr.tailscale-up",
             "Tailscale status --json reports Running", "the next health run reads a running node",
             "Tailscale down on this Mac"),
        ]
        for key, text, owner, remediation, verify, clear, dedup in cases:
            b = lib.breach_from_finding(finding(key, text))
            with self.subTest(key):
                self.assertEqual((b.action.owner, b.action.remediation, b.action.verify, b.action.auto_clear),
                                 (owner, remediation, verify, clear))
                self.assertEqual(b.dedup_key, dedup)
        prebrief = lib.breach_from_finding(finding("calendar_prebrief_missed_run", PREBRIEF))
        self.assertEqual((prebrief.dedup_key, prebrief.action.owner), ("#665", "joe-desk session"))
        self.assertTrue(prebrief.action.remediation.startswith("read out/calendar-prebrief-joe-launchd.log"))
        self.assertIsNone(lib.breach_from_finding(finding("export_receipt", "STALE vendors.xlsx")).action)

    def test_reader_phrases_still_appear_in_their_readers(self):
        """A phrase that drifted from its reader's add-loop body would make the
        consumer file a duplicate beside the reader's loop."""
        lib = load()
        sources = {"production_uptime": "tools/uptime_health.py", "jev_live_outage": "tools/jev_outage_health.py",
                   "grok_session": "ops/grok_session.py"}
        self.assertEqual(set(sources), set(lib.READER_LOOP_PHRASES))
        for key, phrases in lib.READER_LOOP_PHRASES.items():
            text = (REPO / sources[key]).read_text()
            for phrase in phrases:
                self.assertIn(phrase, text, f"{key}: {phrase!r} no longer in {sources[key]}")

    def test_wiring_runs_the_consumer_and_keeps_default_health_read_only(self):
        health = (REPO / "tools" / "health-check.py").read_text()
        self.assertRegex(health, r"_breach_consumer\(\)\n    print\(_HEALTH_COMPLETION_MARKER\)",
                         "the consumer must run before the completion marker, which stays last")
        self.assertIn("if ACT_MODE:", health)
        self.assertIn('elif arg == "--act":', health)
        self.assertIn('subject=_seat_line.split()[1]', health, "one finding per seat")
        nightly = (REPO / "bin" / "nightly.sh").read_text()
        self.assertIn('./run.sh health --act', nightly)
        run_sh = (REPO / "run.sh").read_text()
        self.assertIn('health)       shift; "$PY" "$REPO/tools/health-check.py" "$@" ;;', run_sh)
        self.assertNotRegex(LIB_PATH.read_text().splitlines()[0], r"^#!", "a library, not a new entry point")


if __name__ == "__main__":
    unittest.main()
