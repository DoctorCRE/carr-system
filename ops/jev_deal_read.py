# -*- coding: utf-8 -*-
"""jev_deal_read.py -- read what the record actually says about a live deal.

WHAT THIS ANSWERS, AND WHAT IT REFUSES TO ANSWER. Joe asked for deal close
likelihood. Before writing a line of it this module's author measured what the
record layer holds per live deal, and the measurement changed the build:

    live deals ................................................. 74
    free-text evidence under 200 characters .................... 67
    free-text evidence over 200 characters ...................... 7
    deals carrying a document set ............................... 1
    deals carrying key dates .................................... 1
    closed deals over the same floor ............................ 2 of 99

A likelihood for the 67 would be the model reading a client's name and a phase
label and inventing the rest, and it would look exactly as confident as the
seven real ones. So this module GATES ON EVIDENCE: a deal under the floor is
reported as unanswerable and no question is asked about it. The thin book is
the finding, not a thing to paper over.

AND IT CANNOT BE VALIDATED AGAINST OUTCOMES. There are 99 closed deals carrying
a real won or lost result, which is exactly the held-out slice this work owes.
Two of them clear the same floor, and BOTH WERE WON -- there is not one losing
deal on record with enough written down to learn a loss from. Six carry any free
text at all. The rest hold a name, a type and a lane, so the only honest thing
derivable from them is a base rate per deal type, which is arithmetic and
belongs in SQL. No number this module returns has been checked against a known
outcome, and none may be described as predictive until it has.

A COUNT THAT LOOKED LIKE EVIDENCE AND WAS NOT: the first pass here said six
closed deals carried evidence, because the length of an EMPTY legacy activity
array -- the two characters of "[]" -- was being summed into every row. Six was
the count of rows with any free text; the count that matters is two.

IT IS A LIBRARY AND MUST STAY ONE. No shebang, no main guard -- either makes a
.py file a registered script entrypoint in the sealed source inventory, moves
the frontier and owes a forward-only registry successor. The detector is a
regex over the whole file and does not know what a docstring is, so the
construct is described and never spelled. Callers are entrypoints that already
exist; generators/build-deal-room.py is the one this ships wired to.

THE THREE QUESTIONS, one of each shape, asked in ONE request because they are
independent judgments over the same state:

  movement       Score  -- how far the recorded evidence shows the transaction
                          has moved toward a signature, on five rungs that each
                          describe a concrete recorded situation.
  waiting_on     Choice -- who or what the next step is waiting on, with an
                          explicit option for "the record does not say".
  silence_is_bad Noul   -- whether the measured days of silence are a sign of
                          trouble GIVEN what the deal was waiting on.

The Noul is deliberately not "has this deal been quiet". Quiet is a date
subtraction and code does it below, exactly and for free; asking a model to
detect a condition code already knows duplicates the deterministic pass and
teaches nothing. What code cannot do is rule on whether a particular silence
MATTERS, and that is the question asked.

DEGRADING IS PART OF THE CONTRACT. No credential, no network, a refused
request: read_deals returns bundles with `judged=False` and a reason, and the
Deal Room renders exactly as it did before this module existed.
"""
import datetime
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from ops import typesafe_client as ts  # noqa: E402

# MEASURED, not chosen: 67 of 74 live deals sit under this, and the probe in
# ops/judgment-operational-selftest.py shows what the answers look like below
# it. Raising it narrows the book further; lowering it starts inventing.
EVIDENCE_FLOOR_CHARS = 200

# The state guard in typesafe_client is per request. One deal's bundle is well
# under it, but a pathological carr_status could approach it, so each free-text
# part is clipped here rather than discovered at the boundary.
CLIP = 3000

# The exporter role reads VIEWS, not tables -- `permission denied for table
# deal` is what a direct read earns. The v_deal_room_* family is the granted
# surface and is also the right one: it is what the Deal Room itself is built
# from, so this judgment reads exactly what the room shows.
DEAL_SQL = """
select r.id, r.name, r.phase, r.type as deal_type, r.segment, r.city, r.owner,
       r.next_date, r.operating_state, r.parking_reason, r.parking_note,
       e.lane, e.source_row
  from v_deal_room_deal r
  join v_export_deals e on e.id = r.id
 where e.outcome is null
 order by r.name
"""

NOTE_SQL = """
select n.deal_id, n.created_at::date, n.kind, n.text
  from v_deal_room_note n join v_export_deals e on e.id = n.deal_id
 where e.outcome is null order by n.created_at
"""

ACTIVITY_SQL = """
select a.deal_id, a.occurred_at::date, a.kind, a.summary, a.detail
  from v_deal_room_activity a join v_export_deals e on e.id = a.deal_id
 where e.outcome is null order by a.occurred_at
"""

ACTION_SQL = """
select a.deal_id, a.updated_at::date, a.status, a.description, a.owner, a.due_on
  from v_deal_room_action a join v_export_deals e on e.id = a.deal_id
 where e.outcome is null order by a.updated_at
"""

NEGOTIATION_SQL = """
select n.deal_id, n.proposed_on, n.round_no, n.side, n.rate_amount,
       n.rate_basis, n.free_rent_months, n.term_months, n.note
  from v_deal_room_negotiation n join v_export_deals e on e.id = n.deal_id
 where e.outcome is null order by n.proposed_on
"""

CRITICAL_DATE_SQL = """
select c.deal_id, c.due_on, c.kind, c.status, c.note
  from v_deal_room_critical_date c join v_export_deals e on e.id = c.deal_id
 where e.outcome is null order by c.due_on
"""


def _connect():
    from exporters.common import connect
    return connect()


def _clip(value):
    text = "" if value is None else str(value).strip()
    return text[:CLIP]


def _legacy_activity(source_row):
    """The imported activity log, flattened to lines. Shape varies by vintage."""
    log = (source_row or {}).get("activity")
    if not isinstance(log, list):
        return []
    lines = []
    for item in log:
        if isinstance(item, dict):
            when = item.get("date") or item.get("when") or ""
            what = item.get("text") or item.get("summary") or ""
            lines.append(f"{when} {what}".strip())
        elif item:
            lines.append(str(item))
    return [line for line in lines if line]


def bundles(conn=None):
    """One evidence bundle per live deal, with the evidence already counted.

    Every free-text surface the record layer holds about a deal is gathered
    here in one place: the imported carr_status narrative and next_step, the
    legacy activity log, deal notes, logged activity, open actions, negotiation
    rounds and critical dates. `evidence_chars` is the sum of their lengths and
    is what the floor is applied to -- structured fields are deliberately NOT
    counted, because a phase label is present on all 74 live deals and counting
    it would put every deal over any floor while adding nothing to read.

    LAST TOUCH IS DERIVED FROM EVENTS, not from `updated_at`. A record-keeping
    timestamp moves when a migration backfills a column, so every live deal
    reads as touched within 90 days and the number means nothing. The latest
    dated note, activity, action or negotiation round is the date on which
    something actually happened, and a deal with none of those has no last
    touch at all -- which is itself the answer, and is passed through as None.
    """
    own = conn is None
    conn = conn or _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(DEAL_SQL)
            cols = [d[0] for d in cur.description]
            deals = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute(NOTE_SQL)
            notes = cur.fetchall()
            cur.execute(ACTIVITY_SQL)
            acts = cur.fetchall()
            cur.execute(ACTION_SQL)
            actions = cur.fetchall()
            cur.execute(NEGOTIATION_SQL)
            rounds = cur.fetchall()
            cur.execute(CRITICAL_DATE_SQL)
            dates = cur.fetchall()
    finally:
        if own:
            conn.close()

    _TODAY = datetime.date.today()
    history = {}
    touched = {}

    def add(deal_id, when, line):
        if not line:
            return
        history.setdefault(deal_id, []).append(
            f"{when} {line}" if when else line)
        # A FUTURE date is not a last touch. Actions and critical dates carry
        # due dates, and feeding one in produced a deal reported as quiet for
        # minus nine hundred days. Only a date that has already happened moves
        # the marker; the line still goes into the history either way.
        if when and when <= _TODAY and (deal_id not in touched
                                        or when > touched[deal_id]):
            touched[deal_id] = when

    for deal_id, when, kind, text in notes:
        add(deal_id, when, f"[note/{kind}] {_clip(text)}")
    for deal_id, when, kind, summary, detail in acts:
        body = " ".join(p for p in (_clip(summary), _clip(detail)) if p)
        add(deal_id, when, f"[{kind}] {body}")
    for deal_id, when, status, description, owner, due_on in actions:
        due = f", due {due_on}" if due_on else ""
        add(deal_id, when,
            f"[action/{status}] {_clip(description)} (owner {owner}{due})")
    for (deal_id, when, round_no, side, rate, basis, free_rent,
         term, note) in rounds:
        terms = ", ".join(p for p in (
            f"rate {rate} {basis}" if rate else "",
            f"term {term} months" if term else "",
            f"{free_rent} months free" if free_rent else "") if p)
        add(deal_id, when,
            f"[negotiation round {round_no}, {side}] {terms} {_clip(note)}")
    for deal_id, when, kind, status, note in dates:
        add(deal_id, when, f"[critical date/{kind}, {status}] {_clip(note)}")

    today = _TODAY
    out = []
    for row in deals:
        deal_id = row["id"]
        source_row = row.get("source_row")
        if not isinstance(source_row, dict):
            source_row = {}
        status = _clip(source_row.get("carr_status"))
        next_step = source_row.get("next_step")
        if isinstance(next_step, dict):
            next_step_text = _clip(next_step.get("text"))
            next_step_due = next_step.get("due") or None
        else:
            next_step_text, next_step_due = _clip(next_step), None
        legacy = _legacy_activity(source_row)
        lines = legacy + history.get(deal_id, [])

        evidence_chars = (len(status) + len(next_step_text)
                          + sum(len(x) for x in lines))

        last = touched.get(deal_id)
        days_quiet = (today - last).days if last else None

        out.append({
            "deal_id": str(deal_id),
            "name": row["name"],
            "client": row.get("client_name"),
            "phase": row.get("phase"),
            "deal_type": row.get("deal_type"),
            "lane": row.get("lane"),
            "segment": row.get("segment"),
            "city": row.get("city"),
            "owner": row.get("owner"),
            "operating_state": row.get("operating_state"),
            "parking_reason": row.get("parking_reason"),
            "parking_note": _clip(row.get("parking_note")) or None,
            "next_step": next_step_text,
            "next_step_due": str(next_step_due) if next_step_due else None,
            "next_date": str(row["next_date"]) if row.get("next_date") else None,
            "status_narrative": status,
            "history": lines,
            "last_recorded_event": str(last) if last else None,
            "days_since_record_touched": days_quiet,
            "evidence_chars": evidence_chars,
            "has_evidence": evidence_chars >= EVIDENCE_FLOOR_CHARS,
        })
    return out


def state_for(bundle):
    """The named fields one deal's questions are answered against.

    Deliberately NOT the whole bundle: accuracy falls as a state fills with
    detail unrelated to the decision, so the identifiers, the evidence counter
    and the bookkeeping stay out of the request.
    """
    try:
        due = datetime.date.fromisoformat(str(bundle.get("next_step_due"))[:10])
        due_relation = "past" if due < datetime.date.today() else "future_or_today"
    except (TypeError, ValueError):
        due_relation = "unknown"
    return {
        "deal": {
            "name": bundle["name"],
            "client": bundle["client"],
            "recorded_phase": bundle["phase"],
            "transaction_type": bundle["deal_type"],
            "practice_segment": bundle["segment"],
            "city": bundle["city"],
            "carr_agent": bundle["owner"],
            "next_step_on_file": bundle["next_step"] or None,
            "next_step_due_relation": due_relation,
            "status_narrative": bundle["status_narrative"] or None,
            "history": bundle["history"],
            "days_since_the_record_was_touched":
                bundle["days_since_record_touched"],
        },
    }


MOVEMENT_LEVELS = [
    "The record shows a client and a stated space requirement, but no specific "
    "property has been identified as a candidate.",
    "One or more specific properties have been identified and are being "
    "researched, toured or compared, and no written offer has gone out.",
    "A letter of intent, offer or proposal has been sent to a landlord or "
    "seller and their response is still outstanding.",
    "The parties have exchanged at least one counter and are negotiating "
    "specific economic terms such as rate, term, concessions or improvements.",
    "The economic terms are settled and what remains is drafting, reviewing or "
    "signing the lease or purchase document.",
]

WAITING_OPTIONS = {
    "client": "The next step belongs to the tenant or buyer client: a decision, "
              "a reply, an approval, or information only they can supply.",
    "counterparty": "The next step belongs to the other side of this "
                    "transaction -- the landlord or seller, or anyone acting "
                    "for them, including their listing broker, their property "
                    "manager and their attorney.",
    "carr": "The next step belongs to the CARR agent: something to research, "
            "assemble, send or schedule that has not gone out yet.",
    "market": "Nobody owes a reply. No acceptable property exists yet and the "
              "transaction is waiting on inventory to appear.",
    "third_party": "The next step belongs to someone on NEITHER side of the "
                   "transaction: a lender, an architect, a contractor, a "
                   "franchisor, or a municipal or permitting authority. An "
                   "attorney is not this -- an attorney belongs to whichever "
                   "side retained them.",
    "client_timing": "Nobody owes a reply and nothing is blocked. The client's "
                     "own timeline has not arrived yet.",
    "not_recorded": "The record does not say what this transaction is waiting "
                    "on.",
}


def questions(bundle):
    """The three judgments, one of each shape, over one deal's state."""
    return {
        "movement": ts.score(
            "Read only what `deal` records. How far has this commercial real "
            "estate transaction actually moved toward a signed lease or "
            "purchase agreement? Judge from what the record shows has "
            "happened, not from the `deal.recorded_phase` label, which is "
            "maintained by hand and can lag.",
            MOVEMENT_LEVELS,
        ),
        "waiting_on": ts.choice(
            "Read only what `deal` records. Who or what is this transaction "
            "waiting on for its very next step? Choose who owes the next move, "
            "not who owes the most work overall.",
            WAITING_OPTIONS,
        ),
        "silence_is_bad": ts.noul(
            "`deal.days_since_the_record_was_touched` days have passed with "
            "nothing new recorded on this transaction. Given what `deal` says "
            "it was last waiting on, is that silence a sign this transaction "
            "is in trouble?",
            true="The wait is longer than the step described in the record "
                 "normally takes, and nothing in the record explains the gap.",
            false="The silence fits what the record says is being waited on: a "
                  "normal turnaround, a date still in the future, or a client "
                  "timeline that has not arrived.",
        ),
    }


def read_deal(bundle, *, api_key=None, timeout=20.0, opener=None):
    """Judge one deal, or say why it was not judged. Never raises."""
    if not bundle["has_evidence"]:
        return dict(bundle, judged=False, reason=(
            f"The record holds {bundle['evidence_chars']} characters of "
            f"evidence about this deal, under the {EVIDENCE_FLOOR_CHARS}"
            "-character floor. There is nothing here to read."))
    # The state is built OUTSIDE the guard on purpose. A malformed bundle is a
    # fault in this file, and the first version caught it alongside the network
    # and reported "the judgment did not run: 'client'" -- a programming error
    # wearing a service outage's clothes, in the one place nobody would look.
    state, asked = state_for(bundle), questions(bundle)
    try:
        answer = _semantic().ask(state, asked, caller="jev_deal_read", version="vendor-v1", client=ts, timeout=timeout, api_key=api_key,
                        opener=opener, work_class="app_runtime")
    except Exception as exc:  # the room must build whatever the service does
        return dict(bundle, judged=False, reason=f"the judgment did not run: {exc}")
    answers = answer.get("answers") or {}
    try:
        movement = answers["movement"]["score"]
        waiting = answers["waiting_on"]["choice"]
        silence = answers["silence_is_bad"]["noul"]
    except (KeyError, TypeError) as exc:
        return dict(bundle, judged=False,
                    reason=f"the answer did not carry the expected keys: {exc}")
    return dict(
        bundle, judged=True, reason=None,
        movement=movement,
        movement_confidence=answers["movement"].get("confidence"),
        # THE LEVELS ARE NUMBERED FROM ZERO, verified against a live legend on
        # 2026-09-18, not assumed: a five-rung score returns 0.0 through 4.0
        # and its probabilities are keyed "0".."4". Subtracting one here -- the
        # first version did -- shifts every reported rung down by one and reads
        # as the model being wrong about deals it had judged correctly.
        movement_level=MOVEMENT_LEVELS[
            min(len(MOVEMENT_LEVELS) - 1, max(0, int(round(movement))))],
        movement_rung=int(round(movement)) + 1,
        movement_rungs=len(MOVEMENT_LEVELS),
        waiting_on=waiting,
        waiting_on_confidence=answers["waiting_on"].get("confidence"),
        silence_is_bad=silence,
    )


def read_deals(*, conn=None, api_key=None, timeout=20.0, limit=None):
    """Every live deal, judged where there is evidence and marked where not.

    The caller gets one list and can render it without knowing whether the
    service was reachable: a deal that was not judged carries `judged=False`
    and a `reason` written for a person to read.
    """
    try:
        found = bundles(conn=conn)
    except Exception as exc:
        raise ts.TypeSafeError(f"could not read the deal record: {exc}") from None
    if limit:
        found = found[:limit]
    return [read_deal(b, api_key=api_key, timeout=timeout) for b in found]


def summarise(results):
    """The counts a reader needs before trusting any individual answer."""
    judged = [r for r in results if r.get("judged")]
    return {
        "deals": len(results),
        "judged": len(judged),
        "unjudged": len(results) - len(judged),
        "no_evidence": sum(1 for r in results
                           if not r.get("judged") and not r.get("has_evidence")),
        "waiting_on": _tally(judged, "waiting_on"),
    }


def _tally(rows, field):
    out = {}
    for row in rows:
        out[row.get(field)] = out.get(row.get(field), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def as_json(results):
    return json.dumps(results, indent=2, sort_keys=True, default=str)


def _semantic():
    import importlib.util
    spec = importlib.util.spec_from_file_location("jev_semantic", os.path.join(REPO, "ops", "jev_semantic.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
