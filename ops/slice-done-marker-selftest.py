#!/usr/bin/env python3
"""Behavioural selftest for ops/slice-done-marker.py.

The server is an in-memory fake of the 0628 doors: it keeps registrations,
bindings, release members and append-only marks; derives each criterion's
allowed kinds from its wording (the same rules as
ops.slice_criterion_allowed_kinds) and refuses the seat any other kind, any
refusal_proof, a shipped_release that names no member of this slice, and a
portfolio key other than DoctorCre-v5; treats a seat shipped_release binding as
a PROPOSAL that proves nothing until a partner confirms it; resolves evidence
refs itself (a shipped_release ref must be the bound member; a live_check ref
must be the fake's successful receipt; an accepted_record or effect-free ref
must be the current acceptance of the DoctorCre-v5 portfolio while it is intact
and, for effect-free, effect-less); recomputes each criterion's
candidate_passes on EVERY read; and refuses completion unless every criterion
resolves -- so the marker's own claims, and its previous marks, are never what
passes a slice. git and Jev are fakes too; nothing here touches a network or a
database. The real SQL semantics are proven by
ops/slice-done-marker-local-pg-gate.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("slice_done_marker", HERE / "slice-done-marker.py")
assert spec and spec.loader
sdm = importlib.util.module_from_spec(spec)
sys.modules["slice_done_marker"] = sdm
spec.loader.exec_module(sdm)

SRC = "V5-F08 restore proof (#10)"
CATALOG = [
    {"proposed_id": "V5-F08", "title": "Backup and restore", "item_kind": "coding_slice",
     "checkable_done": ["scanner flags PHI in backups", "restore from independent copy succeeds"]},
    {"proposed_id": "V5-J303", "title": "Tour delivery", "item_kind": "coding_slice",
     "checkable_done": ["client-shared tours see only ruled fields"]},
    {"proposed_id": "V5-R01", "title": "Pilot", "item_kind": "coding_slice",
     "checkable_done": ["Joe pilot observed for two weeks"]},
    {"proposed_id": "V5-R03", "title": "Notifications", "item_kind": "coding_slice",
     "checkable_done": ["quiet hours respected"]},
    {"proposed_id": "V5-D03", "title": "Recording successor", "item_kind": "deferred_successor",
     "checkable_done": ["retention policy decided"]},
    {"proposed_id": "V5-S00", "title": "Portfolio constitution", "item_kind": "coding_slice",
     "checkable_done": ["exact node/edge/child counts and acyclicity pass",
                        "self-review, stale hash and executable-effect negatives refuse",
                        "accepted portfolio creates zero ops.job/capability/product effects"]},
]
F08_SHIP, F08_RESTORE = CATALOG[0]["checkable_done"]
S00_COUNTS, S00_NEG, S00_EFFECTS = CATALOG[-1]["checkable_done"]
SHA_S00 = "d" * 40
PORTFOLIO_RECEIPT = "00000000-0000-4000-8000-0000000000aa"
SHA_F08 = "a" * 40
SHA_J303 = "b" * 40
SHA_R03 = "c" * 40
REL1, REL2 = "1" * 40, "2" * 40
RESTORE_RECEIPT = "00000000-0000-4000-8000-00000000abcd"


def allowed_kinds(criterion: str) -> list[str]:
    """The server's wording rules (ops.slice_criterion_allowed_kinds)."""
    c = criterion.lower()
    if "restor" in c:
        return ["live_check:staging_restore_only_result"]
    if re.search(r"refus|negative|bypass", c):
        return []
    if "acyclic" in c:
        return ["accepted_record:portfolio_revision_acceptance"]
    if "zero" in c and "effect" in c:
        return ["live_check:portfolio_acceptance_effect_free"]
    if re.search(r"observ|pilot|decid|measur|partner|joe|dell|week|month|adopt|survey|feedback|interview|one-use|fresh exact", c):
        return []
    return ["shipped_release:"]


class FakeServer:
    def __init__(self, catalog=CATALOG, releases=(("r-1", REL1), ("r-2", REL2)), restore_receipt=RESTORE_RECEIPT):
        self.catalog = catalog
        self.releases = list(releases)
        self.restore_receipt = restore_receipt
        self.registered: dict[str, list[str]] = {}
        self.bindings: dict[tuple[str, str], list[dict]] = {}
        self.members: list[dict] = []
        self.marks: dict[str, list[dict]] = {}
        self.proposals: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.refuse_complete = False
        self.portfolio_receipt = PORTFOLIO_RECEIPT
        self.portfolio_intact = True
        self.portfolio_effects = 0

    def __call__(self, verb, args):
        self.calls.append((verb, json.loads(json.dumps(args))))
        return getattr(self, "v_" + verb.replace("-", "_"))(args)

    def writes(self):
        return [v for v, _ in self.calls if v not in ("read-doctrine", "read-slice-completion", "list-shipped-releases")]

    def v_read_doctrine(self, _args):
        return {"sections": [{"section_key": sdm.CATALOG_SECTION,
                              "body": {"text": json.dumps({"slices": self.catalog})}}]}

    def v_list_shipped_releases(self, _args):
        return {"releases": [{"release_key": k, "git_sha": s} for k, s in self.releases]}

    def v_record_release_slice_members(self, args):
        assert args["release_key"] in dict(self.releases)
        for m in args["members"]:
            if not any(x["release_key"] == args["release_key"] and x["commit_sha"] == m["commit_sha"]
                       and x["slice_id"] == m["slice_id"] for x in self.members):
                self.members.append({**m, "release_key": args["release_key"], "id": f"mem-{len(self.members)}"})
        return {"ok": True}

    def v_register_slice_criteria_from_catalog(self, args):
        assert set(args) == {"idempotency_key", "slice_id"}, "the marker must never pass criteria"
        sid = args["slice_id"]
        assert sid not in self.registered
        self.registered[sid] = next(s["checkable_done"] for s in self.catalog if s["proposed_id"] == sid)
        return {"ok": True}

    def v_bind_slice_criterion_evidence(self, args):
        sid, c, kind = args["slice_id"], args["criterion"], args["evidence_kind"]
        if kind == "refusal_proof":
            raise sdm.MarkerError("refusal_proof_has_no_server_gate_source")
        if self.held(sid):
            raise sdm.MarkerError("slice_mark_held_by_partner")
        if f"{kind}:{args.get('live_check_source') or ''}" not in allowed_kinds(c):
            raise sdm.MarkerError("automation_binding_kind_not_allowed")
        if args.get("live_check_source") in sdm.PORTFOLIO_SOURCES and args.get("live_check_key") != "DoctorCre-v5":
            raise sdm.MarkerError("automation_portfolio_key_not_catalog_portfolio")
        if self.bindings.get((sid, c)):
            raise sdm.MarkerError("criterion_already_bound")
        if kind == "shipped_release" and not any(m["id"] == args.get("bound_member_id") and m["slice_id"] == sid
                                                 for m in self.members):
            raise sdm.MarkerError("shipped_release_binding_requires_this_slice_member")
        self.bindings.setdefault((sid, c), []).append({
            "kind": kind, "source": args["live_check_source"], "key": args.get("live_check_key"),
            "member": args.get("bound_member_id"), "via": "automation"})
        return {"ok": True}

    def partner_bind(self, sid, criterion, kind, member=None):
        self.bindings.setdefault((sid, criterion), []).append(
            {"kind": kind, "source": None, "key": None, "member": member, "via": "authority"})

    def confirm(self, sid, criterion):
        """A partner confirms the seat's shipped_release proposal (same member)."""
        proposal = next(b for b in self.bindings[(sid, criterion)] if b["via"] == "automation")
        self.partner_bind(sid, criterion, "shipped_release", proposal["member"])

    def effective(self, sid, criterion):
        rows = self.bindings.get((sid, criterion)) or []
        partner = [b for b in rows if b["via"] == "authority"]
        if partner:
            return partner[-1]
        seat = [b for b in rows if b["via"] == "automation" and b["kind"] != "shipped_release"]
        return seat[0] if seat else None

    def proposal(self, sid, criterion):
        rows = self.bindings.get((sid, criterion)) or []
        if any(b["via"] == "authority" for b in rows):
            return None
        return next(({"bound_member_id": b["member"]} for b in rows
                     if b["via"] == "automation" and b["kind"] == "shipped_release"), None)

    def _portfolio_ok(self, b, ref):
        return (ref is not None and ref == self.portfolio_receipt and self.portfolio_intact
                and b.get("key") == "DoctorCre-v5")

    def resolve(self, sid, criterion, ref):
        b = self.effective(sid, criterion)
        if not b or ref is None:
            return False
        if b["kind"] == "shipped_release":
            return ref == b["member"] and any(m["id"] == ref and m["slice_id"] == sid for m in self.members)
        if b["kind"] == "accepted_record":
            return b["source"] == "portfolio_revision_acceptance" and self._portfolio_ok(b, ref)
        if b["kind"] == "live_check" and b["source"] == "portfolio_acceptance_effect_free":
            return self._portfolio_ok(b, ref) and self.portfolio_effects == 0
        if b["kind"] == "live_check":
            return ref == self.restore_receipt
        return False

    def candidate(self, b):
        if not b:
            return None
        if b["kind"] == "shipped_release":
            return b["member"]
        if b["kind"] == "accepted_record" or b["source"] == "portfolio_acceptance_effect_free":
            return self.portfolio_receipt
        if b["kind"] == "live_check":
            return self.restore_receipt
        return None

    def receipt(self, sid, items):
        return [{"criterion": i["criterion"], "evidence_ref": i["evidence_ref"],
                 "pass": self.resolve(sid, i["criterion"], i["evidence_ref"])} for i in items]

    def v_mark_slice_progress(self, args):
        sid = args["slice_id"]
        if self.held(sid):
            raise sdm.MarkerError("slice_mark_held_by_partner")
        self.marks.setdefault(sid, []).append({"id": f"mark-{sid}-{len(self.marks.get(sid, []))}",
            "status": args["status"], "reason": args.get("reason"), "marked_via": "automation",
            "criteria_receipt": self.receipt(sid, args["criteria_receipt"])})
        return {"ok": True}

    def v_propose_slice_completion(self, args):
        """Automation's only door to the terminal state: a proposal, never a mark."""
        sid = args["slice_id"]
        if self.refuse_complete or self.held(sid):
            raise sdm.MarkerError("slice_completion_proposal_refused")
        rec = self.receipt(sid, args["criteria_receipt"])
        if not all(r["pass"] for r in rec) or {r["criterion"] for r in rec} != set(self.registered[sid]):
            raise sdm.MarkerError("slice_completion_proposal_requires_every_criterion_proven")
        marks = self.marks.get(sid) or []
        self.proposals.setdefault(sid, []).append({"id": f"prop-{sid}-{len(self.proposals.get(sid, []))}",
            "criteria_receipt": rec, "basis": len(marks)})
        return {"ok": True}

    def awaiting(self, sid):
        props = self.proposals.get(sid) or []
        return bool(props) and props[-1]["basis"] == len(self.marks.get(sid) or []) and not self.held(sid)

    def partner_confirms(self, sid):
        """confirm-slice-completions: re-evaluate the latest proposal NOW."""
        if not self.awaiting(sid):
            return "not_awaiting"
        rec = self.receipt(sid, self.proposals[sid][-1]["criteria_receipt"])
        if not all(r["pass"] for r in rec):
            return "not_proven"
        self.marks.setdefault(sid, []).append({"id": f"mark-{sid}-{len(self.marks.get(sid, []))}",
            "status": "complete", "reason": "partner", "marked_via": "authority", "criteria_receipt": rec})
        return "confirmed"

    def hold(self, sid):
        self.marks.setdefault(sid, []).append({"id": f"hold-{sid}", "status": "blocked", "reason": "partner",
                                               "marked_via": "authority_hold", "criteria_receipt": []})

    def held(self, sid):
        m = self.marks.get(sid)
        return bool(m) and m[-1]["marked_via"] in ("authority", "authority_hold")

    def v_read_slice_completion(self, args):
        sid = args["slice_id"]
        # Exactly the projection ops.read_slice_done_state returns.
        members = [{"id": m["id"], "release_key": m["release_key"], "commit_sha": m["commit_sha"],
                    "pr_number": m.get("pr_number"), "subject": m["subject"],
                    "attribution": m["attribution"]} for m in self.members
                   if m["slice_id"] == sid]
        criteria = []
        for c in self.registered.get(sid, []):
            b = self.effective(sid, c)
            criteria.append({
                "criterion": c, "evidence_kind": b["kind"] if b else "unbound",
                "binding_source": f"binding:{b['via']}" if b else "registration",
                "automation_bound": any(x["via"] == "automation" for x in self.bindings.get((sid, c)) or []),
                "allowed_kinds": allowed_kinds(c), "proposal": self.proposal(sid, c),
                "bound_member_id": b.get("member") if b else None,
                "live_check_source": b["source"] if b else None, "live_check_key": b.get("key") if b else None,
                "live_check_candidate": self.candidate(b),
                "candidate_passes": self.resolve(sid, c, self.candidate(b))})
        marks = self.marks.get(sid) or []
        entry = next((s for s in self.catalog if s["proposed_id"] == sid), None)
        return {"ok": True, "done_state": {
            "registered": sid in self.registered, "criteria": criteria, "release_members": members,
            "catalog_allowed_kinds": ({c: allowed_kinds(c) for c in entry["checkable_done"]}
                                      if entry and sid not in self.registered else None),
            "held_by_partner": self.held(sid), "latest_mark": marks[-1] if marks else None,
            "latest_proposal": (self.proposals.get(sid) or [None])[-1],
            "awaiting_partner_confirmation": self.awaiting(sid)}}


def fake_git(commits, reach):
    """commits: [(sha, subject)] newest first; reach: {release_sha: set(commit shas)}."""
    def run(*args):
        if args[0] == "log":
            return "".join(f"{sha}\x1f{subject}\x1e" for sha, subject in commits)
        if args[0] == "rev-list":
            return "\n".join(sorted(reach.get(args[-1], set())))
        raise AssertionError(args)
    return run


COMMITS = [(SHA_S00, "S00 portfolio negatives gate (#9)"),
           (SHA_R03, "R03 sweep runner keeps HEAD==pin (#850)"),
           (SHA_J303, "J303 client-shared tours allowlist (#12)"),
           (SHA_F08, SRC)]


def marker(server, commits=COMMITS, reach=None):
    reach = reach if reach is not None else {REL1: {SHA_F08, SHA_S00}, REL2: {SHA_F08, SHA_R03, SHA_S00}}
    return sdm.Marker(call=server, git_run=fake_git(commits, reach), out=lambda _s: None)


def by_id(outcomes):
    return {o.slice_id: o for o in outcomes}


def member_of(server, sid):
    return next(m["id"] for m in server.members if m["slice_id"] == sid)


class Attribution(unittest.TestCase):
    IDS = {c["proposed_id"] for c in CATALOG} | {"V5-F03", "V5-A00", "V5-RW02"}

    def test_explicit_and_bare_ids(self):
        self.assertEqual(sdm.attribute("Make a restore prove it (V5-F08) (#1240)", self.IDS), [("V5-F08", "explicit")])
        self.assertEqual(sdm.attribute("Deliver F03 validator migration (#1037)", self.IDS), [("V5-F03", "bare_id")])
        self.assertEqual(sdm.attribute("Add A00 benchmark draft storage", self.IDS), [("V5-A00", "bare_id")])
        self.assertEqual(sdm.attribute("Add the reconciliation kernel (V5-RW02)", self.IDS), [("V5-RW02", "explicit")])

    def test_roadmap_ids_and_ux_packages_are_not_attributed(self):
        self.assertEqual(sdm.attribute("R03 sweep runner: keep the HEAD==pin invariant", self.IDS), [])
        self.assertEqual(sdm.attribute("Add DoctorCRE V5-UX-C02 resource dashboard", self.IDS), [])
        self.assertEqual(sdm.attribute("D03 retention and M01 cadence", self.IDS), [])


class Run(unittest.TestCase):
    def test_backfill_proposes_and_marks_in_progress_blocked_and_parked(self):
        server = FakeServer()
        out = by_id(marker(server).run())
        # F08: the restore criterion binds as the server allows and passes; the
        # shipped criterion is only a PROPOSAL, so the slice is not complete.
        self.assertEqual(out["V5-F08"].status, "in_progress", out["V5-F08"])
        self.assertIn(f"{F08_SHIP} -> proposed release member {member_of(server, 'V5-F08')} awaiting partner "
                      "confirmation", out["V5-F08"].reason)
        self.assertEqual(server.bindings[("V5-F08", F08_SHIP)],
                         [{"kind": "shipped_release", "source": None, "key": None,
                           "member": member_of(server, "V5-F08"), "via": "automation"}])
        # J303's merge is in no complete release yet.
        self.assertEqual(out["V5-J303"].status, "in_progress")
        self.assertIn("no sole shipped change of this slice to propose yet", out["V5-J303"].reason)
        # A runtime outcome no kind can show stays unbound: blocked, named.
        self.assertEqual(out["V5-R01"].status, "blocked")
        self.assertIn("Joe pilot observed for two weeks -> no server-resolvable evidence kind", out["V5-R01"].reason)
        self.assertEqual((out["V5-D03"].status, out["V5-D03"].reason), ("blocked", "parked by Joe"))
        # Every catalog slice ends marked; nothing completed on a proposal.
        self.assertEqual(set(server.marks), {c["proposed_id"] for c in CATALOG})
        self.assertFalse(any(m["status"] == "complete" for ms in server.marks.values() for m in ms))

    def test_the_marker_proposes_and_only_a_partner_confirmation_completes(self):
        server = FakeServer()
        marker(server).run()
        server.confirm("V5-F08", F08_SHIP)
        out = by_id(marker(server).run({"V5-F08"}))
        # Every criterion proven: the marker PROPOSES; it never writes complete.
        self.assertEqual((out["V5-F08"].status, out["V5-F08"].wrote), ("proposed", "proposed"), out["V5-F08"])
        self.assertIn("awaiting partner confirmation", out["V5-F08"].reason)
        self.assertNotEqual(server.marks["V5-F08"][-1]["status"], "complete")
        self.assertNotIn("auto-mark-slice-completion", [v for v, _ in server.calls])
        self.assertTrue(all(r["pass"] for r in server.proposals["V5-F08"][-1]["criteria_receipt"]))
        # A later run finds the same pending proposal and writes nothing.
        before = len(server.writes())
        out = by_id(marker(server).run({"V5-F08"}))
        self.assertEqual((out["V5-F08"].status, out["V5-F08"].wrote), ("proposed", "unchanged"))
        self.assertEqual(server.writes()[before:], [])
        # The partner confirms; the slice is complete and the marker then leaves it alone.
        self.assertEqual(server.partner_confirms("V5-F08"), "confirmed")
        self.assertEqual(server.marks["V5-F08"][-1]["marked_via"], "authority")
        before = len(server.writes())
        out = by_id(marker(server).run({"V5-F08"}))
        self.assertEqual(out["V5-F08"].wrote, "skipped")
        self.assertEqual(server.writes()[before:], [])

    def test_no_marker_call_can_set_complete(self):
        server = FakeServer()
        marker(server).run()
        server.confirm("V5-F08", F08_SHIP)
        server.partner_bind("V5-S00", S00_NEG, "shipped_release", member_of(server, "V5-S00"))
        marker(server).run()
        self.assertTrue(server.proposals)
        verbs = {v for v, _ in server.calls}
        self.assertFalse(verbs & {"auto-mark-slice-completion", "mark-slice-completion", "confirm-slice-completions"})
        self.assertFalse(any(m["status"] == "complete" for ms in server.marks.values() for m in ms))

    def test_the_kind_is_the_servers_and_refusal_proof_is_never_bound(self):
        server = FakeServer()
        marker(server).run()
        for (sid, c), rows in server.bindings.items():
            for b in rows:
                if b["via"] == "automation":
                    self.assertIn(f"{b['kind']}:{b['source'] or ''}", allowed_kinds(c), (sid, c))
        kinds = [a["evidence_kind"] for v, a in server.calls if v == "bind-slice-criterion-evidence"]
        self.assertNotIn("refusal_proof", kinds)
        self.assertNotIn(("V5-S00", S00_NEG), server.bindings)
        self.assertNotIn(("V5-R01", "Joe pilot observed for two weeks"), server.bindings)

    def test_membership_goes_to_the_earliest_containing_release_and_skips_roadmap_ids(self):
        server = FakeServer()
        marker(server).run()
        self.assertEqual(sorted((m["release_key"], m["slice_id"]) for m in server.members),
                         [("r-1", "V5-F08"), ("r-1", "V5-S00")])

    def test_a_second_run_writes_nothing(self):
        server = FakeServer()
        marker(server).run()
        before = len(server.writes())
        marker(server).run()
        self.assertEqual(server.writes()[before:], [])

    def test_a_partner_hold_is_skipped_untouched(self):
        server = FakeServer()
        marker(server).run()
        server.hold("V5-J303")
        before = len(server.writes())
        out = by_id(marker(server).run())
        self.assertEqual(out["V5-J303"].wrote, "skipped")
        self.assertEqual(server.writes()[before:], [])

    def test_two_shipped_members_are_ambiguous_and_propose_nothing(self):
        server = FakeServer()
        second = "e" * 40
        commits = COMMITS + [(second, "V5-F08 backup scanner follow-up (#11)")]
        reach = {REL1: {SHA_F08, SHA_S00, second}, REL2: {SHA_F08, SHA_R03, SHA_S00, second}}
        out = by_id(marker(server, commits, reach).run())
        self.assertNotIn(("V5-F08", F08_SHIP), server.bindings)
        self.assertEqual(out["V5-F08"].status, "in_progress")
        self.assertIn(f"{F08_SHIP} -> no sole shipped change of this slice to propose yet", out["V5-F08"].reason)

    def test_server_refusal_of_the_proposal_is_recorded_as_in_progress(self):
        server = FakeServer()
        marker(server).run()
        server.confirm("V5-F08", F08_SHIP)
        server.refuse_complete = True
        out = by_id(marker(server).run({"V5-F08"}))
        self.assertEqual(out["V5-F08"].status, "in_progress")
        self.assertIn("server refused the completion proposal", out["V5-F08"].reason)

    def test_missing_live_receipt_names_the_source(self):
        server = FakeServer(restore_receipt=None)
        out = by_id(marker(server).run())
        self.assertEqual(out["V5-F08"].status, "in_progress")
        self.assertIn("no staging_restore_only_result row", out["V5-F08"].reason)

    def test_a_partner_decided_criterion_is_never_reclassified(self):
        # A partner explicitly UNBOUND a criterion the wording would allow as
        # shipped: the marker must leave it alone and report it blocked.
        # F08 has a shipped member Jev would match, so only the partner
        # decision stops a proposal.
        server = FakeServer()
        server.registered["V5-F08"] = [F08_SHIP, F08_RESTORE]
        server.partner_bind("V5-F08", F08_SHIP, "unbound")
        out = by_id(marker(server).run({"V5-F08"}))
        self.assertNotIn(F08_SHIP, [a.get("criterion") for v, a in server.calls
                                    if v == "bind-slice-criterion-evidence"])
        self.assertEqual(out["V5-F08"].status, "blocked")
        self.assertIn(f"{F08_SHIP} -> unbound by a partner", out["V5-F08"].reason)

    def test_s00_blocks_on_the_negatives_until_a_partner_decides_them(self):
        server = FakeServer()
        out = by_id(marker(server).run({"V5-S00"}))
        self.assertEqual(out["V5-S00"].status, "blocked", out["V5-S00"])
        self.assertIn(f"{S00_NEG} -> no server-resolvable evidence kind for this wording; a partner decides",
                      out["V5-S00"].reason)
        b = {c: server.bindings[("V5-S00", c)][0] for c in (S00_COUNTS, S00_EFFECTS)}
        self.assertEqual((b[S00_COUNTS]["kind"], b[S00_COUNTS]["source"], b[S00_COUNTS]["key"]),
                         ("accepted_record", "portfolio_revision_acceptance", "DoctorCre-v5"))
        self.assertEqual((b[S00_EFFECTS]["kind"], b[S00_EFFECTS]["source"], b[S00_EFFECTS]["key"]),
                         ("live_check", "portfolio_acceptance_effect_free", "DoctorCre-v5"))
        # The partner decides the negatives (the shipped PR whose gate ran them).
        member = member_of(server, "V5-S00")
        server.partner_bind("V5-S00", S00_NEG, "shipped_release", member)
        out = by_id(marker(server).run({"V5-S00"}))
        self.assertEqual(out["V5-S00"].status, "proposed", out["V5-S00"])
        refs = {r["criterion"]: r["evidence_ref"] for r in server.proposals["V5-S00"][-1]["criteria_receipt"]}
        self.assertEqual(refs, {S00_COUNTS: PORTFOLIO_RECEIPT, S00_NEG: member, S00_EFFECTS: PORTFOLIO_RECEIPT})

    def _s00_complete(self, server):
        marker(server).run({"V5-S00"})
        server.partner_bind("V5-S00", S00_NEG, "shipped_release", member_of(server, "V5-S00"))

    def test_state_comes_from_live_reads_not_the_previous_proposal(self):
        # Proposed once; then the accepted revision stops recomputing intact.
        # The earlier proposal proves nothing: the next run reads the live
        # candidate_passes, marks the slice in_progress (which leaves the
        # proposal stale, so a partner cannot confirm it) and says why.
        server = FakeServer()
        self._s00_complete(server)
        marker(server).run({"V5-S00"})
        self.assertTrue(server.awaiting("V5-S00"))
        server.portfolio_intact = False
        out = by_id(marker(server).run({"V5-S00"}))
        self.assertEqual(out["V5-S00"].status, "in_progress")
        self.assertEqual(server.marks["V5-S00"][-1]["status"], "in_progress")
        self.assertFalse(server.awaiting("V5-S00"))
        self.assertIn("does not pass on a live read", out["V5-S00"].reason)
        self.assertNotIn("bind-slice-criterion-evidence", server.writes()[-1:])

    def test_confirmation_re_evaluates_the_proposal(self):
        server = FakeServer()
        self._s00_complete(server)
        marker(server).run({"V5-S00"})
        server.portfolio_effects = 1
        self.assertEqual(server.partner_confirms("V5-S00"), "not_proven")

    def test_an_effect_in_the_acceptance_window_is_named_missing(self):
        server = FakeServer()
        self._s00_complete(server)
        server.portfolio_effects = 1
        out = by_id(marker(server).run({"V5-S00"}))
        self.assertEqual(out["V5-S00"].status, "in_progress")
        self.assertIn("accepted portfolio creates zero ops.job/capability/product effects -> the "
                      "portfolio_acceptance_effect_free for DoctorCre-v5 row", out["V5-S00"].reason)

    def test_no_accepted_portfolio_leaves_it_missing(self):
        server = FakeServer()
        self._s00_complete(server)
        server.portfolio_receipt = None
        out = by_id(marker(server).run({"V5-S00"}))
        self.assertEqual(out["V5-S00"].status, "in_progress")
        self.assertIn("no portfolio_revision_acceptance for DoctorCre-v5 row", out["V5-S00"].reason)

    def test_dry_run_writes_nothing_and_says_what_it_would_bind(self):
        server = FakeServer()
        m = marker(server)
        m.dry_run = True
        out = by_id(m.run())
        self.assertEqual(server.writes(), [])
        self.assertEqual(out["V5-D03"].status, "blocked")
        self.assertIn(f"{F08_RESTORE} -> [dry-run] would bind live_check staging_restore_only_result",
                      out["V5-F08"].reason)

    def test_idempotency_keys_are_stable_and_distinct(self):
        self.assertEqual(sdm.ikey("register", "V5-F08"), sdm.ikey("register", "V5-F08"))
        self.assertNotEqual(sdm.ikey("register", "V5-F08"), sdm.ikey("register", "V5-F09"))


if __name__ == "__main__":
    unittest.main()
