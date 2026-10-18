#!/usr/bin/env python3
# doctrine: doctorcre-v5-astra-integration-review
"""slice-done-marker.py — make the DoctorCRE v5 slice done-record fire.

WHY THIS EXISTS (2026-09-25, Joe: "fix the done record issue. If it's not
firing we need to know why and solve it"). #1245 shipped the Q153 doors
(register / mark progress / mark complete / read) and nothing ever called
them: production held 0 registrations, 0 criteria and 0 marks, so every one of
the 36 catalog slices read marked:false. Migration 0628 gives the automated
seat its own doors and two new server-resolved evidence kinds; this script is
the process that calls them. The release pipeline runs it after every
SHIPPED worker release (ops/release-pipeline.py, best-effort), and it runs by
hand for the backfill:

    ./.venv/bin/python ops/slice-done-marker.py            # every catalog slice
    ./.venv/bin/python ops/slice-done-marker.py --dry-run  # compute, write nothing
    ./.venv/bin/python ops/slice-done-marker.py --slices V5-F08,V5-J303

WHAT ONE RUN DOES, per catalog slice (the catalog is doctrine
`doctorcre-v5-astra-integration-review`, section
`v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09`):

  1. Membership. Every commit on origin/main since the catalog era whose
     subject names a catalog slice is attributed to it (Jev semantic_creation
     A1, 2026-09-25: an explicit `V5-<id>` always; a bare F/A/J/S id such as
     `F03` or `J303` as a whole word; never a bare R/D/M/W id, which collides
     with the older roadmap's R03 etc.). Each attributed commit is recorded
     against the EARLIEST complete production release that contains it
     (record-release-slice-members). The server accepts members only for a
     complete production release.
  2. Registration. An unregistered slice is registered with
     register-slice-criteria-from-catalog: the server reads the criteria from
     the catalog itself; this script never passes one.
  3. Binding. The KIND is the server's, not this script's: each criterion's
     done_state carries `allowed_kinds`, derived server-side from its catalog
     wording (ops.slice_criterion_allowed_kinds), and the bind door refuses
     anything else. For a criterion still unbound (and never bound by a
     partner): a live_check / accepted_record kind is bound as allowed; a
     shipped_release kind is proposed only when the slice has exactly ONE
     shipped release member, and that binding is a
     PROPOSAL the server never treats as effective until a partner confirms
     it (the Worker holds no GitHub credential, so the server cannot verify
     the PR itself). An empty allowlist (refusals, runtime outcomes) binds
     nothing: the criterion stays unbound for a partner.
  4. Evidence, per criterion, from LIVE reads only: the server's candidate row
     and its `candidate_passes`, which read-slice-completion recomputes from
     the live rows on every read. A pending proposal is reported as the
     missing fact "awaiting partner confirmation". The previous mark is never
     evidence of anything; the server re-resolves every ref on completion.
  5. Mark (Jev semantic_creation S1): every criterion has evidence ->
     propose-slice-completion (the server recomputes and may refuse). The
     marker NEVER sets complete, and no door lets it: a proposal changes no
     mark, and the slice becomes complete only when a partner confirms it with
     confirm-slice-completions (many slices in one act;
     pending-slice-completion-proposals lists them), which re-evaluates every
     criterion at that moment. Any criterion no kind allows (or a partner
     unbound), or a slice parked by Joe -> blocked with the reason; otherwise
     in_progress naming each missing fact (a shipped criterion with no matched
     change yet, a binding proposal awaiting partner confirmation, a row that
     does not pass). --dry-run reads the server's catalog_allowed_kinds for an
     unregistered slice and reports what it would bind. The previous mark and
     proposal are read for one thing only: not re-writing an identical one. A
     partner-held slice (including a partner-confirmed complete) is skipped.

It never writes a file outside out/slice-done-marker/, never force-anything,
and a failure is a nonzero exit the pipeline records without failing the
release it follows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.record_call import call_verb  # noqa: E402

CATALOG_DOC = "doctorcre-v5-astra-integration-review"
CATALOG_SECTION = "v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09"
# Commits before this cannot belong to the 2026-09-09 catalog's slices.
HISTORY_SINCE = "2026-09-01"
# Joe's ruling: these are parked, not failing.
PARKED = {"V5-D03": "parked by Joe", "V5-D04": "parked by Joe"}
BARE_ID = re.compile(r"(?<![\w-])((?:F|A|S)\d{2}|J\d{3})(?![\w-])")
PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")
NAMESPACE = uuid.UUID("8f0b3a52-5f0e-4c55-9d7c-6b1a0f3e2d11")
OUT_DIR = REPO / "out" / "slice-done-marker"

# The one portfolio the accepted-record sources resolve against.
PORTFOLIO_REF = "DoctorCre-v5"
# Sources whose key is the portfolio ref.
PORTFOLIO_SOURCES = {"portfolio_revision_acceptance", "portfolio_acceptance_effect_free"}
# Automation never sets complete: a proven slice is PROPOSED, and a partner
# confirms it (confirm-slice-completions re-evaluates every criterion then).
AWAITING = "every criterion proven; proposed complete, awaiting partner confirmation (confirm-slice-completions)"


class MarkerError(RuntimeError):
    pass


def ikey(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(json.dumps(p, sort_keys=True, default=str) for p in parts)))


def attribute(subject: str, catalog_ids: set[str]) -> list[tuple[str, str]]:
    """(slice_id, attribution) for every catalog slice a commit subject names."""
    found: dict[str, str] = {}
    for sid in sorted(catalog_ids, key=len, reverse=True):
        if re.search(r"(?<![\w-])" + re.escape(sid) + r"(?![\w-])", subject):
            found[sid] = "explicit"
    for m in BARE_ID.finditer(subject):
        sid = f"V5-{m.group(1)}"
        if sid in catalog_ids and sid not in found:
            found[sid] = "bare_id"
    return sorted(found.items())


# ── adapters (replaced by fakes in ops/slice-done-marker-selftest.py) ─────────

def run_sh_call(verb: str, args: dict) -> dict:
    """The sanctioned Bash door, through lib/record_call: anything but ok raises."""
    result = call_verb(verb, args, timeout=300)
    if not result.ok:
        raise MarkerError(result.describe())
    return result.reply


def git(*args: str) -> str:
    proc = subprocess.run(["git", "-C", str(REPO), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise MarkerError(f"git {args[0]}: {(proc.stderr or '').strip()[:300]}")
    return proc.stdout


# ── the marker ────────────────────────────────────────────────────────────────

@dataclass
class SliceOutcome:
    slice_id: str
    status: str
    reason: str
    wrote: str
    refs: dict[str, str | None] = field(default_factory=dict)


class Marker:
    def __init__(self, *, call: Callable[[str, dict], dict], git_run: Callable[..., str],
                 dry_run: bool = False, out: Callable[[str], None] = print):
        self.call, self.git, self.dry_run, self.out = call, git_run, dry_run, out
    # -- catalog -----------------------------------------------------------
    def catalog(self) -> list[dict]:
        doc = self.call("read-doctrine", {"document": CATALOG_DOC})
        for section in doc.get("sections", []):
            if section.get("section_key") == CATALOG_SECTION:
                body = section.get("body") or {}
                text = body.get("text") if isinstance(body, dict) else body
                slices = json.loads(str(text)).get("slices") or []
                if not slices:
                    raise MarkerError("the catalog section names no slices")
                return slices
        raise MarkerError(f"catalog section {CATALOG_SECTION} not found in {CATALOG_DOC}")

    # -- membership ----------------------------------------------------------
    def attributed_commits(self, catalog_ids: set[str]) -> list[dict]:
        raw = self.git("log", "--first-parent", f"--since={HISTORY_SINCE}",
                       "--format=%H%x1f%s%x1e", "origin/main")
        commits = []
        for rec in raw.split("\x1e"):
            parts = rec.strip("\n").split("\x1f")
            if len(parts) < 2 or not re.fullmatch(r"[0-9a-f]{40}", parts[0].strip()):
                continue
            sha, subject = parts[0].strip(), parts[1].strip()
            for sid, how in attribute(subject, catalog_ids):
                pr = PR_NUMBER.search(subject)
                commits.append({"slice_id": sid, "commit_sha": sha, "subject": subject[:400],
                                "pr_number": int(pr.group(1)) if pr else None, "attribution": how})
        return commits

    def sync_membership(self, catalog_ids: set[str], known: set[tuple[str, str, str]],
                        wanted: set[str] | None = None) -> int:
        commits = self.attributed_commits(catalog_ids)
        if not commits:
            return 0
        releases = self.call("list-shipped-releases", {"since": HISTORY_SINCE}).get("releases", [])
        reach: list[tuple[str, set[str]]] = []
        for rel in releases:
            try:
                reach.append((rel["release_key"], set(self.git(
                    "rev-list", f"--since={HISTORY_SINCE}", rel["git_sha"]).split())))
            except MarkerError:
                continue          # a release SHA this checkout does not hold
        by_release: dict[str, list[dict]] = {}
        for c in commits:
            first = next((key for key, shas in reach if c["commit_sha"] in shas), None)
            if first is None or (first, c["slice_id"], c["commit_sha"]) in known \
                    or (wanted is not None and c["slice_id"] not in wanted):
                continue
            member = {k: c[k] for k in ("slice_id", "commit_sha", "pr_number", "subject", "attribution")}
            by_release.setdefault(first, []).append(member)
        written = 0
        for key, members in by_release.items():
            if self.dry_run:
                self.out(f"  [dry-run] record-release-slice-members {key}: {len(members)} member(s)")
                continue
            self.call("record-release-slice-members", {
                "idempotency_key": ikey("members", key, sorted((m["slice_id"], m["commit_sha"]) for m in members)),
                "release_key": key, "members": members})
            written += len(members)
        return written

    @staticmethod
    def match(item: dict, criteria: list[str], members: list[dict]) -> dict[str, str | None]:
        """Propose the slice's sole shipped release member for each criterion.

        The server already scopes members to this slice; their subjects are
        membership hints, never acceptance. A proposal is not effective until
        a partner confirms it, and two or more members are ambiguous, so the
        criterion stays unbound for review.
        """
        ids = [m.get("id") for m in members]
        sole = ids[0] if len(ids) == 1 and isinstance(ids[0], str) and ids[0] else None
        return {c: sole for c in criteria}

    # -- one slice ---------------------------------------------------------
    def read(self, slice_id: str) -> dict:
        return self.call("read-slice-completion", {"slice_id": slice_id}).get("done_state") or {}

    def mark_slice(self, item: dict) -> SliceOutcome:
        sid = item["proposed_id"]
        state = self.read(sid)
        if not state.get("registered"):
            if self.dry_run:
                self.out(f"  [dry-run] register-slice-criteria-from-catalog {sid}")
                allowed = state.get("catalog_allowed_kinds") or {}
                pending = [{"criterion": c, "evidence_kind": "unbound", "binding_source": "registration",
                             "automation_bound": False, "allowed_kinds": allowed.get(c) or []}
                           for c in item.get("checkable_done") or []]
                state = {"registered": False, "criteria": pending, "release_members": state.get("release_members", [])}
            else:
                self.call("register-slice-criteria-from-catalog",
                          {"idempotency_key": ikey("register", sid), "slice_id": sid})
                state = self.read(sid)
        if state.get("held_by_partner"):
            latest = state.get("latest_mark") or {}
            return SliceOutcome(sid, latest.get("status") or "?", "held by partner", "skipped")
        criteria = [row["criterion"] for row in state.get("criteria", [])]
        kinds = {row["criterion"]: row for row in state.get("criteria", [])}

        if sid in PARKED:
            return self._write(sid, state, "blocked", PARKED[sid], {c: None for c in criteria})

        # Binding: only criteria still unbound, never bound by automation, and
        # not partner-decided (a partner binding reports binding_source
        # binding:authority -- including an explicit partner unbind). The kind
        # is the one the server allows for the wording; nothing else is tried.
        members = state.get("release_members") or []
        # (a pending proposal is automation_bound, so it is never re-proposed)
        todo = [c for c in criteria if kinds[c]["evidence_kind"] == "unbound"
                and not kinds[c].get("automation_bound") and kinds[c].get("binding_source") == "registration"]
        to_ship = [c for c in todo if "shipped_release:" in (kinds[c].get("allowed_kinds") or [])]
        matched = self.match(item, to_ship, members) if to_ship and members else {}
        bound_any = False
        would: dict[str, str] = {}
        for c in todo:
            allowed = kinds[c].get("allowed_kinds") or []
            if "shipped_release:" in allowed:
                if matched.get(c):
                    self._bind(sid, c, "shipped_release", None, "sole shipped member of the slice: proposed PR, "
                               "awaiting partner confirmation", member=matched[c])
                    bound_any = True
                    would[c] = f"a shipped_release proposal on member {matched[c]}"
                continue
            for entry in allowed:
                kind, _, source = entry.partition(":")
                self._bind(sid, c, kind, source or None, "server-derived allowed kind",
                           key=PORTFOLIO_REF if source in PORTFOLIO_SOURCES else None)
                bound_any = True
                would[c] = f"{kind} {source}".strip()
                break
        if bound_any and not self.dry_run:
            state = self.read(sid)
            kinds = {row["criterion"]: row for row in state.get("criteria", [])}

        refs: dict[str, str | None] = {}
        missing: list[str] = []
        unbound: list[str] = []
        for c in criteria:
            k = kinds[c]
            kind = k["evidence_kind"]
            cand = k.get("live_check_candidate")
            if self.dry_run and c in would:
                refs[c] = None
                missing.append(f"{c} -> [dry-run] would bind {would[c]}")
            elif kind in ("shipped_release", "live_check", "accepted_record"):
                # The server's live recompute of its own candidate.
                refs[c] = cand if cand and k.get("candidate_passes") is True else None
                if refs[c] is None:
                    what = (kind if kind == "shipped_release" else f"{k.get('live_check_source')}"
                            + (f" for {k['live_check_key']}" if k.get("live_check_key") else ""))
                    missing.append(f"{c} -> " + (f"no {what} row" if not cand else
                                                 f"the {what} row ({cand}) does not pass on a live read"))
            elif kind == "unbound" and k.get("proposal"):
                refs[c] = None
                missing.append(f"{c} -> proposed release member {k['proposal'].get('bound_member_id')} "
                               "awaiting partner confirmation")
            elif kind == "unbound" and "shipped_release:" in (k.get("allowed_kinds") or []) \
                    and k.get("binding_source") == "registration":
                # Still waiting on a shipped change: in progress, not blocked.
                refs[c] = None
                missing.append(f"{c} -> no sole shipped change of this slice to propose yet")
            elif kind == "unbound":
                refs[c] = None
                unbound.append(f"{c} -> " + ("unbound by a partner" if k.get("binding_source") == "binding:authority"
                                             else "no server-resolvable evidence kind for this wording; "
                                                  "a partner decides"))
            else:
                refs[c] = None
                missing.append(f"{c} -> {kind} evidence is partner-registered; the marker does not resolve it")

        if not missing and not unbound and criteria:
            proposal = state.get("latest_proposal") or {}
            prior = {el.get("criterion"): el.get("evidence_ref") for el in proposal.get("criteria_receipt") or []}
            if state.get("awaiting_partner_confirmation") and prior == refs:
                return SliceOutcome(sid, "proposed", AWAITING, "unchanged", refs)
            if self.dry_run:
                return SliceOutcome(sid, "proposed", "all criteria proven; would propose complete", "dry-run", refs)
            try:
                self.call("propose-slice-completion", {
                    "idempotency_key": ikey("propose", sid, (state.get("latest_mark") or {}).get("id"),
                                            proposal.get("id"), refs),
                    "slice_id": sid, "reason": "every criterion proven by server-resolved evidence",
                    "criteria_receipt": [{"criterion": c, "evidence_ref": refs[c]} for c in criteria]})
                return SliceOutcome(sid, "proposed", AWAITING, "proposed", refs)
            except MarkerError as exc:
                missing.append(f"server refused the completion proposal: {str(exc)[:300]}")
        if unbound:
            return self._write(sid, state, "blocked", "blocked: " + "; ".join(unbound + missing), refs)
        return self._write(sid, state, "in_progress", "missing: " + "; ".join(missing), refs)

    def _bind(self, sid: str, criterion: str, kind: str, source: str | None, reason: str, *,
              key: str | None = None, member: str | None = None) -> None:
        if self.dry_run:
            self.out(f"  [dry-run] bind {sid} / {criterion[:60]} -> {kind}" + (f" {source}" if source else ""))
            return
        self.call("bind-slice-criterion-evidence", {
            "idempotency_key": ikey("bind", sid, criterion, kind, source, key, member), "slice_id": sid,
            "criterion": criterion, "evidence_kind": kind, "live_check_source": source,
            "live_check_key": key, "bound_member_id": member, "reason": reason})

    def _write(self, sid: str, state: dict, status: str, reason: str, refs: dict[str, str | None]) -> SliceOutcome:
        latest = state.get("latest_mark") or {}
        prior_refs = {el.get("criterion"): el.get("evidence_ref") for el in latest.get("criteria_receipt") or []}
        if latest.get("status") == status and latest.get("reason") == reason and prior_refs == refs:
            return SliceOutcome(sid, status, reason, "unchanged", refs)
        if self.dry_run or not refs:
            return SliceOutcome(sid, status, reason, "dry-run" if self.dry_run else "no-criteria", refs)
        self.call("mark-slice-progress", {
            "idempotency_key": ikey("progress", sid, latest.get("id"), status, reason, refs),
            "slice_id": sid, "status": status, "reason": reason,
            "criteria_receipt": [{"criterion": c, "evidence_ref": r} for c, r in refs.items()]})
        return SliceOutcome(sid, status, reason, "marked", refs)

    # -- a run ---------------------------------------------------------------
    def run(self, only: set[str] | None = None) -> list[SliceOutcome]:
        catalog = self.catalog()
        ids = {s["proposed_id"] for s in catalog}
        wanted = [s for s in catalog if not only or s["proposed_id"] in only]
        known: set[tuple[str, str, str]] = set()
        for s in wanted:
            for m in self.read(s["proposed_id"]).get("release_members") or []:
                known.add((m["release_key"], s["proposed_id"], m["commit_sha"]))
        added = self.sync_membership(ids, known, {s["proposed_id"] for s in wanted})
        self.out(f"slice-done-marker: {added} new release member(s)")
        outcomes = []
        for item in wanted:
            try:
                outcomes.append(self.mark_slice(item))
            except MarkerError as exc:
                outcomes.append(SliceOutcome(item["proposed_id"], "error", str(exc)[:400], "error"))
        return outcomes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print; write nothing")
    ap.add_argument("--slices", default="", help="comma-separated slice ids (default: every catalog slice)")
    ap.add_argument("--release-key", default="", help="the release that just shipped (logged)")

    args = ap.parse_args(argv)
    marker = Marker(call=run_sh_call, git_run=git, dry_run=args.dry_run)
    if args.release_key:
        print(f"slice-done-marker: after release {args.release_key}")
    try:
        outcomes = marker.run({s.strip() for s in args.slices.split(",") if s.strip()} or None)
    except MarkerError as exc:
        print(f"slice-done-marker: FAILED — {exc}", file=sys.stderr)
        return 2
    for o in outcomes:
        print(f"  {o.slice_id:9} {o.status:11} [{o.wrote}] {o.reason[:160]}")
    if not args.dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "last-run.json").write_text(json.dumps(
            [o.__dict__ for o in outcomes], indent=1, default=str))
    return 1 if any(o.status == "error" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
