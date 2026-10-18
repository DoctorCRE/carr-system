"""The defect bridge: every recorded defect gets a Work Request (WR-000269).

Joe's ruling, 2026-10-12: a detected bug is recorded AND addressed, never
stepped over. `record-defect` writes the first half into public.defect; until
this module nothing carried a defect on to the work that fixes it. 593 defects
sat in the log that day, every one with the census state `fix_untracked`.

WHAT "ADDRESSED" MEANS HERE, measured against the live schema on 2026-10-12:

  * public.defect has NO link column and NO fix-receipt column (id,
    occurred_on, defect_class, claimed, actual, source_unread, rule_violated,
    detected_by, session_key, cost_note, created_at, created_by). The census
    leg for it says so in its own reason code, `fix_status_not_recorded`.
  * report-problem does not STORE its `situation`: it is the doctrine search
    query that picks the request's source section. A defect id written only
    there would be unreadable the moment the call returned.

So a defect is LINKED when a Work Request that is not declined or superseded
names it in its title, desired outcome or acceptance criteria (the three text
fields work-request-card reads back): either the full id, or `defect <8 hex>`
the way WR-000118 already names defect 5dd69e83. A confirmed-closed request
still links: that is the only fix receipt this schema can express. Everything
else is UNADDRESSED.

WHAT IT FILES: one Work Request per defect class per open set, through
report-problem, titled `Defect class <class>: <n> unaddressed`, naming every id
of the set as `defect:<uuid>` in the desired outcome (and in overflow
acceptance criteria when a class outgrows it). Filing is what links: the next
run reads the ids back from the card and files nothing.

DEDUPE IS THE RECORD LAYER, NEVER A LOCAL FILE. Before filing, the existing
requests are searched (unfinished-work, open and library, then each candidate's
card). The idempotency key is derived from the class and its id set, so a run
killed after the server committed replays the same request instead of a twin.
A read that fails, or a census that comes back short of its own count, files
NOTHING: an unproven "no request exists" is how duplicates are born.

The door is injectable (`Door`: verb, args -> VerbResult) so the selftest
replays a fake record layer; the real one is lib.record_call.call_verb, the
allowlisted `./run.sh call` door.
"""
from __future__ import annotations

import argparse
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from lib.record_call import OK, REFUSED, VerbResult, call_verb

Door = Callable[[str, Mapping[str, Any]], VerbResult]

PRODUCT_TASK = "DoctorCRE unattended delivery (plan C)"
TITLE_PREFIX = "Defect class "
PAGE = 500                      # unfinished-work's own maximum page
MAX_FILINGS = 10                # Work Requests filed per run; the rest wait a night
CARD_WORKERS = 8
KEY_NAMESPACE = uuid.UUID("5b0d6c1e-2a8f-4f43-9d0e-2b7f0c4a9e69")

# report-problem's own limits (mcp-server/src/work-request-intake.js).
TITLE_MAX, SITUATION_MAX, OUTCOME_MAX, AC_MAX, AC_TEXT_MAX = 200, 1000, 2000, 12, 500

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_SHORT = re.compile(r"\bdefects?[\s:#]+([0-9a-f]{8})\b", re.I)
_DEFECT_PARTIAL_REASON = "fix_status_not_recorded"

BOUND_ACTION = (
    "on breach: the nightly 'defect bridge' step (ops/defect-bridge.py) files one Work "
    "Request per defect class through report-problem, titled 'Defect class <class>: <n> "
    f"unaddressed' and naming every defect id, at most {MAX_FILINGS} a night, largest class "
    "first; owner claude; verify: ops/defect-bridge.py --dry-run lists nothing to file; "
    "auto-clears when 0")


class DoorFailure(Exception):
    """A read the bridge cannot prove complete. Nothing is filed after one."""


@dataclass(frozen=True)
class Defect:
    id: str
    defect_class: str
    claimed: str
    recorded_at: str


@dataclass(frozen=True)
class Filing:
    defect_class: str
    defect_ids: tuple[str, ...]
    payload: dict[str, Any]

    @property
    def title(self) -> str:
        return str(self.payload["title"])


@dataclass
class Survey:
    defects: list[Defect]
    linked: dict[str, str]                       # defect id -> the WR that names it
    filings: list[Filing]
    deferred_classes: list[str] = field(default_factory=list)

    @property
    def unaddressed(self) -> list[Defect]:
        return [d for d in self.defects if d.id not in self.linked]


def default_door(verb: str, args: Mapping[str, Any]) -> VerbResult:
    return call_verb(verb, args)


# ── reads ─────────────────────────────────────────────────────────────────────

def _census(door: Door, kind: str, *, text: str | None = None,
            live_library: bool = False) -> list[dict[str, Any]]:
    """Every unfinished-work row of one kind, all pages, or DoorFailure."""
    args: dict[str, Any] = {"kinds": kind, "limit": PAGE}
    if text:
        args["text"] = text
    if live_library:
        args["live_library"] = True
    items: list[dict[str, Any]] = []
    expected: int | None = None
    cursor: str | None = None
    while True:
        result = door("unfinished-work", {**args, **({"cursor": cursor} if cursor else {})})
        if not result.ok or not isinstance(result.reply, dict):
            raise DoorFailure(f"unfinished-work ({kind}) {result.describe()}")
        reply = result.reply
        cover = [c for c in reply.get("coverage") or [] if c.get("kind") == kind]
        if len(cover) != 1:
            raise DoorFailure(f"unfinished-work ({kind}) returned no coverage row for {kind}")
        state, reason = cover[0].get("state"), cover[0].get("reason")
        if not (state == "complete" or (kind == "defect" and state == "partial"
                                        and reason == _DEFECT_PARTIAL_REASON)):
            raise DoorFailure(f"unfinished-work ({kind}) coverage {state} ({reason})")
        if expected is None:
            expected = cover[0].get("count_total")
        items.extend(i for i in reply.get("items") or [] if i.get("kind") == kind)
        cursor = reply.get("next_cursor")
        if not cursor:
            break
    if not isinstance(expected, int) or len(items) != expected:
        raise DoorFailure(f"unfinished-work ({kind}) read {len(items)} rows of {expected} counted")
    return items


def read_defects(door: Door) -> list[Defect]:
    defects = []
    for row in _census(door, "defect"):
        defect_id = str((row.get("identity") or {}).get("defect_id") or row.get("id") or "")
        cls, sep, claimed = str(row.get("title") or "").partition(": ")
        if not _UUID.fullmatch(defect_id) or not sep:
            raise DoorFailure(f"unfinished-work defect row unreadable: {row.get('id')!r}")
        defects.append(Defect(defect_id.lower(), cls, claimed, str(row.get("last_activity_at") or "")))
    return defects


def _card_text(door: Door, ref: str) -> str:
    result = door("work-request-card", {"work_request": ref})
    if not result.ok or not isinstance(result.reply, dict):
        raise DoorFailure(f"work-request-card {ref} {result.describe()}")
    card = result.reply
    texts = [str(card.get("title") or ""), str(card.get("desired_outcome") or "")]
    texts += [str(ac.get("text") or "") for ac in card.get("acceptance_criteria") or []]
    return "\n".join(texts)


def named_defects(text: str, defect_ids: Iterable[str]) -> set[str]:
    """The defect ids a Work Request's text names: a full id, or `defect <8 hex>`
    when exactly one recorded defect carries that prefix."""
    ids = set(defect_ids)
    found = {m.group(0).lower() for m in _UUID.finditer(text)} & ids
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for defect_id in ids:
        by_prefix[defect_id[:8]].append(defect_id)
    for m in _SHORT.finditer(text):
        owners = by_prefix.get(m.group(1).lower(), [])
        if len(owners) == 1:
            found.add(owners[0])
    return found


def read_links(door: Door, defects: Sequence[Defect]) -> dict[str, str]:
    """defect id -> the live (not declined, not superseded) WR that names it."""
    candidates = set()
    for live_library in (False, True):
        for row in _census(door, "work_request", text="defect", live_library=live_library):
            if not row.get("cancelled"):
                candidates.add(str(row["id"]))
    refs = sorted(candidates)
    with ThreadPoolExecutor(max_workers=CARD_WORKERS) as pool:
        texts = list(pool.map(lambda ref: _card_text(door, ref), refs))
    ids = [d.id for d in defects]
    linked: dict[str, str] = {}
    for ref, text in zip(refs, texts):
        for defect_id in sorted(named_defects(text, ids)):
            linked.setdefault(defect_id, ref)
    return linked


# ── what to file ──────────────────────────────────────────────────────────────

def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build_filing(defect_class: str, members: Sequence[Defect]) -> Filing:
    """One report-problem payload for one class's open set. Ids that do not fit
    the payload's limits stay unaddressed and are filed by a later run."""
    ordered = sorted(members, key=lambda d: (d.recorded_at, d.id))
    tokens = [f"defect:{d.id}" for d in ordered]
    outcome = (
        "Each defect named here is addressed: a merged fix whose pull request names the "
        f"defect id, or a recorded reason none is needed. Filed by the defect bridge "
        f"(WR-000269) for the product task {PRODUCT_TASK}; naming an id here is what links "
        "it, so it is filed once. Defects:")
    criteria = [
        {"id": "AC-1", "text": "Every defect named in this request has a merged fix whose pull "
                               "request names its id, or a recorded reason no fix is needed."},
        {"id": "AC-2", "text": f"The cause shared by class {defect_class} is named, and a gate, "
                               "check or test now catches a recurrence, or the reason none can "
                               "is recorded."},
    ]
    taken = 0
    while taken < len(tokens) and len(outcome) + 1 + len(tokens[taken]) <= OUTCOME_MAX:
        outcome += " " + tokens[taken]
        taken += 1
    while taken < len(tokens) and len(criteria) < AC_MAX:
        text = "Also named by this request:"
        while taken < len(tokens) and len(text) + 1 + len(tokens[taken]) <= AC_TEXT_MAX:
            text += " " + tokens[taken]
            taken += 1
        criteria.append({"id": f"IDS-{len(criteria) - 1}", "text": text})
    covered = ordered[:taken]
    newest = covered[-1]
    situation = (
        f"The defect log holds {taken} recorded defect(s) of class {defect_class} that no "
        f"Work Request names and no fix receipt closes. Newest, recorded "
        f"{newest.recorded_at[:10]}: claimed \"{_clip(newest.claimed, 240)}\"; actual: not "
        "readable through any read verb (unfinished-work carries class and claimed only), "
        f"see v_defect {newest.id}. Product task: {PRODUCT_TASK}. Defect ids:")
    named = 0
    while named < taken and len(situation) + 1 + len(tokens[named]) <= SITUATION_MAX - 40:
        situation += " " + tokens[named]
        named += 1
    if named < taken:
        situation += f" and {taken - named} more in the desired outcome."
    ids = tuple(d.id for d in covered)
    key = uuid.uuid5(KEY_NAMESPACE, f"defect-bridge/v1|{defect_class}|{','.join(sorted(ids))}")
    payload = {
        "idempotency_key": str(key),
        "title": _clip(f"{TITLE_PREFIX}{defect_class}: {taken} unaddressed", TITLE_MAX),
        "situation": situation,
        "desired_outcome": outcome,
        "acceptance_criteria": criteria,
    }
    return Filing(defect_class, ids, payload)


def plan(defects: Sequence[Defect], linked: Mapping[str, str], *,
         max_filings: int = MAX_FILINGS) -> tuple[list[Filing], list[str]]:
    """(filings for this run, classes deferred by the per-run cap): largest
    class first, then the most recently recorded, then by name."""
    by_class: dict[str, list[Defect]] = defaultdict(list)
    for d in defects:
        if d.id not in linked:
            by_class[d.defect_class].append(d)
    newest = {c: max(d.recorded_at for d in members) for c, members in by_class.items()}
    order = sorted(by_class, key=lambda c: c)
    order.sort(key=lambda c: newest[c], reverse=True)
    order.sort(key=lambda c: len(by_class[c]), reverse=True)
    return [build_filing(c, by_class[c]) for c in order[:max_filings]], order[max_filings:]


def survey(door: Door, *, max_filings: int = MAX_FILINGS) -> Survey:
    """Read-only: the defects, which are linked, and what this run would file."""
    defects = read_defects(door)
    linked = read_links(door, defects)
    filings, deferred = plan(defects, linked, max_filings=max_filings)
    return Survey(defects, linked, filings, deferred)


# ── the one write ─────────────────────────────────────────────────────────────

def file_all(door: Door, filings: Sequence[Filing]) -> list[tuple[Filing, VerbResult]]:
    """File each Work Request. A refusal is reported and the next class is
    tried; a transient or failed door stops the run (the key replays next run)."""
    done: list[tuple[Filing, VerbResult]] = []
    for filing in filings:
        result = door("report-problem", filing.payload)
        done.append((filing, result))
        if result.kind not in (OK, REFUSED):
            break
    return done


# ── surfaces ──────────────────────────────────────────────────────────────────

def health_row(found: Survey | None, error: str | None = None) -> tuple[str, int]:
    """The health-check row (rule 590b11e1: the bound action prints inline)."""
    label = f"{'unaddressed defects':<21}"
    if found is None:
        return (f"  ⚠︎ {label}UNKNOWN — {error or 'no survey'}; a failed read is never "
                f"all-clear · {BOUND_ACTION}", 1)
    n = len(found.unaddressed)
    if n == 0:
        return (f"  OK {label}0 of {len(found.defects)} recorded defects lack a Work Request · "
                f"{BOUND_ACTION}", 0)
    classes = len({d.defect_class for d in found.unaddressed})
    return (f"  ⚠︎ {label}{n} of {len(found.defects)} recorded defects in {classes} class(es) "
            f"lack a Work Request · {BOUND_ACTION}", 1)


def render(found: Survey, *, dry_run: bool) -> list[str]:
    unaddressed = found.unaddressed
    lines = [f"defect bridge: {len(found.defects)} recorded, {len(found.linked)} linked to a "
             f"Work Request, {len(unaddressed)} unaddressed in "
             f"{len({d.defect_class for d in unaddressed})} class(es)"]
    verb = "would file" if dry_run else "filing"
    for f in found.filings:
        lines.append(f"  {verb}: {f.title}  [key {f.payload['idempotency_key']}]")
        lines.append(f"      ids: {' '.join(i[:8] for i in f.defect_ids)}")
    if found.deferred_classes:
        lines.append(f"  deferred by the per-run cap of {len(found.filings)}: "
                     f"{len(found.deferred_classes)} more class(es), filed on later runs")
    if not found.filings:
        lines.append("  nothing to file")
    return lines


def main(argv: Sequence[str] | None = None, *, door: Door | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ops/defect-bridge.py",
                                 description="File one Work Request per unaddressed defect class.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read and print; file nothing")
    mode.add_argument("--health-row", action="store_true", help="print the health-check row")
    ap.add_argument("--max-filings", type=int, default=MAX_FILINGS,
                    help=f"Work Requests filed per run (default {MAX_FILINGS})")
    args = ap.parse_args(argv)
    door = door or default_door
    try:
        found = survey(door, max_filings=max(0, args.max_filings))
    except DoorFailure as exc:
        print(health_row(None, str(exc))[0] if args.health_row
              else f"defect bridge: FILED NOTHING — {exc}")
        return 1
    if args.health_row:
        line, rc = health_row(found)
        print(line)
        return rc
    for line in render(found, dry_run=args.dry_run):
        print(line)
    if args.dry_run:
        return 0
    rc = 0
    for filing, result in file_all(door, found.filings):
        reply = result.reply if isinstance(result.reply, dict) else {}
        if result.ok and reply.get("human_ref"):
            print(f"  filed {reply['human_ref']}: {filing.title}")
            found.linked.update({i: str(reply["human_ref"]) for i in filing.defect_ids})
        else:
            print(f"  NOT FILED {filing.title}: {result.describe()}")
            rc = 1
    print(health_row(found)[0])
    return rc
