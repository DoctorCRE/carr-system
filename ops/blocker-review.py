#!/usr/bin/env python3
"""blocker-review.py — test every open loop's stated blocker against reality, and
report only the ones that have DEMONSTRABLY CLEARED.

WHY THIS EXISTS. Joe, 2026-08-10: "You spam loops all day and never built
anything towards actually working through the loops." He is right, and the
numbers say how right: 404 loops created, 161 ever closed, 243 open. Loops are
cheap to create and expensive to close, so the pile only grows.

`add-loop` already refuses an open loop that does not name a blocker from a
closed list — a genuinely good control, added because "later" is not a reason.
But the blocker is captured at creation and NEVER RE-READ. A grep across
pipelines/, exporters/ and tools/health-check.py for `blocker` returns nothing.
So a loop whose counterparty replied three weeks ago is indistinguishable from
one blocked this morning, and both sit in the same undifferentiated 243.

THE POINT OF THIS SCRIPT, stated as the thing it must never become: it is NOT
another report. It answers one question — WHICH OF THESE CAN BE WORKED RIGHT NOW
— and its output is a short list, or nothing. A list of 243 is what we already
have.

WHAT IT CAN AND CANNOT DECIDE. Each blocker class gets a test only where the
record can actually answer it. Where it cannot, the row is left alone and said so
out loud rather than guessed at, because a false "unblocked" costs more than a
missed one: it puts work in front of a partner that is still blocked, and two of
those teach him to stop reading the list.

  counterparty    CLEARABLE IN PRINCIPLE, AND NOT UNDER THIS CREDENTIAL. The test
                  wants any contact-kind activity logged against the party the
                  detail names, since the loop was raised. That reads activity,
                  activity_kind, party and the client/lead/vendor union — four
                  base tables. app_exporter_local, the credential this script
                  runs under, can read NONE of them: measured 2026-08-21,
                  has_table_privilege returns loop_item=True and activity,
                  activity_kind, party, client, lead, vendor all False. The
                  exporter is view-scoped on purpose (79 of 79 views readable,
                  zero base tables), so this is a deliberate boundary and not a
                  missing grant to go and add. Until a view exposes party name
                  beside last contact date, the branch reports itself untestable
                  IN THE OUTPUT rather than crashing or, worse, quietly
                  answering "nothing cleared".
  external_event  CLEARABLE. A dated wait whose date has passed is no longer a
                  wait. due_on is the date; loops carrying none are skipped.
  capability      PARTLY CLEARABLE, and the docstring used to overstate it. It
                  claimed a verb, a view and a credential were all checkable;
                  the code under it did nothing but `unknown += 1`. Two of the
                  three are real and are implemented here: a named RELATION is
                  tested against the catalog, and a named CREDENTIAL against the
                  environment and ~/.config/carr/*.env. A named VERB is NOT
                  tested and must never clear on one — the checkout's tools.js
                  is not proof of what production runs. On 2026-08-21 production
                  sat 12 commits behind main, so a verb sitting in the source
                  tree was genuinely absent from the live Worker. Read the verb
                  count off /release if that test is ever wanted.
  other_lane      CLEARABLE where the row names a loop number. If that loop is
                  closed, this one is no longer waiting on it.
  ruling          NOT CLEARABLE. A ruling is a human's to make and nothing in the
                  record says he has made it.
  human_only      NOT CLEARABLE, by definition. Counted and reported so the size
                  of the genuinely-human pile is visible, which is itself a
                  number worth seeing.

CLOSES NOTHING. Deliberately. This surfaces; a session or a partner decides. An
auto-closer would be the same defect in the other direction — the system deciding
work is done because a proxy went green, which is exactly the "success signal not
derived from the artifact" failure the health check already carries scars from.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.credential_file import carr_config, credential, read_env_file  # noqa: E402
LOOP_REF = re.compile(r"(?:loop\s*#|#)(\d{1,4})", re.I)

# The four base tables the counterparty test would have to join. Named here so
# the privilege probe, the degradation notice and the selftest all read the same
# list — a second copy of a boundary is a second chance to get it wrong, which is
# the lesson GATE_LIVE below already carries.
COUNTERPARTY_TABLES = ("activity", "activity_kind", "party", "client", "lead", "vendor")

# Identifiers a capability blocker can be tested on. Backticked tokens are read
# because that is how these rows are written when they mean a specific thing;
# bare `v_*` and BARE_UPPER_SNAKE are read too because neither shape occurs in
# ordinary prose, so neither can produce the false clear the module's own bar
# forbids ("a false 'unblocked' costs more than a missed one").
_BACKTICKED = re.compile(r"`([^`]{2,64})`")
_RELATION = re.compile(r"^[a-z][a-z0-9_]{2,}$")
_VIEWISH = re.compile(r"\bv_[a-z0-9_]{2,}\b")
_CREDENTIAL = re.compile(r"^[A-Z][A-Z0-9_]{4,}$")
_BARE_CREDENTIAL = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){1,}\b")


def _wrap(text: str, width: int):
    """Wrap without pulling textwrap in for one call site."""
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line); line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def refs_in(detail: str, num: str) -> set[str]:
    """Loop numbers a blocker_detail names, minus the row's own number."""
    return {m for m in LOOP_REF.findall(detail or "")} - {str(num)}


def test_external_event(due_on, today) -> str | None:
    """A dated wait whose date has arrived is no longer a wait."""
    if due_on and due_on <= today:
        return f"the date it was waiting for ({due_on}) has passed"
    return None


def test_other_lane(detail: str, num: str, states) -> str | None:
    """Cleared only when EVERY loop the detail names is closed. `states` is the
    (number, status) rows the caller looked up; an empty list means the detail
    named nothing testable, which is a hold, not a clear."""
    if not refs_in(detail, num):
        return None
    if states and all(st != "open" for _, st in states):
        return ("the loop(s) it waited on are closed: "
                + ", ".join(f"#{n}" for n, _ in states))
    return None


def _capability_identifiers(detail: str):
    """(relations, credentials, verbs) named in a capability blocker.

    A token carrying a hyphen is a VERB and is returned separately so the caller
    can refuse to clear on it. Everything about verbs here is deliberately
    inert."""
    relations, credentials, verbs = set(), set(), set()
    for tok in _BACKTICKED.findall(detail or ""):
        tok = tok.strip()
        if "-" in tok:
            verbs.add(tok)
        elif _CREDENTIAL.match(tok):
            credentials.add(tok)
        elif _RELATION.match(tok):
            relations.add(tok)
    relations |= set(_VIEWISH.findall(detail or ""))
    credentials |= set(_BARE_CREDENTIAL.findall(detail or ""))
    return relations, credentials, verbs


def test_capability(detail: str, relation_exists, credential_exists) -> str | None:
    """Cleared when a RELATION or CREDENTIAL the row named now exists.

    Never cleared on a verb name: see the capability entry in the module
    docstring. `relation_exists` and `credential_exists` are injected so this is
    provable without a database."""
    relations, credentials, _verbs = _capability_identifiers(detail)
    for name in sorted(relations):
        if relation_exists(name):
            return f"`{name}` exists in the catalog now; this row was raised because it did not"
    for name in sorted(credentials):
        if credential_exists(name):
            return f"`{name}` is present on this machine now; this row was raised because it was not"
    return None


def render_degradation(unreadable) -> str:
    """The counterparty notice. It must NAME the tables, because 'some checks
    were skipped' is the kind of line a reader learns to scroll past."""
    if not unreadable:
        return ""
    return ("counterparty rows were NOT tested: this credential cannot read "
            + ", ".join(sorted(unreadable))
            + ". That is the exporter's deliberate view-only scope, not a missing "
              "grant — the fix is a view exposing party name beside last contact "
              "date, not widening the credential.")


def credential_present(name: str) -> bool:
    """Is a named credential on this machine — in the environment, or in any of
    the ~/.config/carr/*.env files the rest of the system reads."""
    if os.environ.get(name):
        return True
    conf = carr_config()
    if not conf.is_dir():
        return False
    for path in sorted(conf.glob("*.env")):
        try:
            if read_env_file(path).get(name):
                return True
        except OSError:
            continue
    return False


def db_url() -> str | None:
    return os.environ.get("CARR_DB_EXPORTER_URL") or os.environ.get("DATABASE_URL") \
        or credential("CARR_DB_EXPORTER_URL", environ={})


def main() -> int:
    url = db_url()
    if not url:
        print("blocker-review: NOT CONFIGURED (no database credential)", file=sys.stderr)
        return 78

    import psycopg
    today = date.today()
    # GATE_LIVE is the INSTANT the Worker carrying the blocker gate reached
    # production, not midnight of the day it shipped. That distinction is not
    # pedantry: the first version of this script used the date, reported ten
    # loops as gate bypasses, and every one of them was created before 22:20 on
    # the day in question — the latest by ONE MINUTE. No add-loop call reaching
    # the old Worker could have been asked for a blocker.
    #
    # migration 0083 already fixed this exact error for v_loop_no_blocker and
    # wrote down why: "a flag that reports fourteen false positives on day one is
    # a check that is chronically red, and a chronically red check detects
    # nothing." This script repeated the mistake within a day of that migration
    # landing, which is the argument for reading the instant from the view rather
    # than restating it here — a second copy of a boundary is a second chance to
    # get it wrong.
    GATE_LIVE = datetime.fromisoformat("2026-08-09T22:20:28.647+00:00")
    cleared, human, unknown, unblockered, leaked = [], 0, 0, 0, []
    untestable_counterparty = 0

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # PROBE THE PRIVILEGE BEFORE USING IT, rather than letting the query
        # raise. This script died here on 2026-08-21 — InsufficientPrivilege on
        # `activity` — and because psycopg aborts the whole transaction on a
        # failed statement, catching it per-row would have needed a savepoint
        # around every counterparty test. Asking once is cheaper and, more to the
        # point, lets the run SAY what it could not do instead of discovering it
        # one row at a time.
        unreadable = []
        for tbl in COUNTERPARTY_TABLES:
            cur.execute("select has_table_privilege(current_user, %s, 'SELECT')", (tbl,))
            row = cur.fetchone()
            # No row at all means the question could not be answered, which is
            # not the same as "yes" and must not be read as one.
            if row is None or not row[0]:
                unreadable.append(tbl)

        cur.execute("""
            select number, owner, domain, blocker_class, blocker_detail, due_on,
                   left(regexp_replace(coalesce(body, title, ''), '[[:space:]]+', ' ', 'g'), 120),
                   created_at::date, created_at
              from loop_item
             where status = 'open' and kind = 'open_loop'
             order by length(number), number""")
        rows = cur.fetchall()

        for num, owner, domain, bclass, bdetail, due_on, gist, raised, created_at in rows:
            detail = bdetail or ""

            if not bclass:
                unblockered += 1
                if created_at >= GATE_LIVE:
                    leaked.append((num, owner, gist))
                continue

            if bclass == "human_only":
                human += 1
                continue

            if bclass == "external_event":
                why = test_external_event(due_on, today)
                if why:
                    cleared.append((num, owner, domain, gist, why))
                continue

            if bclass == "counterparty":
                if unreadable:
                    # Untestable, and counted so the notice below can say how
                    # many rows it applies to. NOT folded into `unknown`: a row
                    # nothing can test and a row this credential happens not to
                    # be able to test are different findings, and merging them
                    # would hide a fixable gap inside a permanent one.
                    untestable_counterparty += 1
                    continue
                # Any contact-kind activity against a party the detail names,
                # since the loop was raised. Name match is deliberately narrow:
                # a full-name match only, because matching a bare first name
                # against 1,222 parties is the contamination the merge rules
                # already forbid.
                cur.execute("""
                    select p.name, max(a.occurred_at)::date
                      from activity a
                      join activity_kind k on k.slug = a.kind and k.is_contact
                      join party p on p.id in (
                            select party_id from client  where id = a.client_id
                      union select party_id from lead    where id = a.lead_id
                      union select party_id from vendor  where id = a.vendor_id)
                     where a.occurred_at::date > %s
                       and position(lower(p.name) in lower(%s)) > 0
                       and length(p.name) > 8
                     group by p.name
                     order by 2 desc limit 1""", (raised, detail))
                hit = cur.fetchone()
                if hit:
                    cleared.append((num, owner, domain, gist,
                                    f"{hit[0]} was contacted {hit[1]}, after this was raised"))
                continue

            if bclass == "other_lane":
                refs = refs_in(detail, num)
                if not refs:
                    unknown += 1
                    continue
                cur.execute("""select number, status from loop_item
                                where number = any(%s) and kind='open_loop'""", (list(refs),))
                why = test_other_lane(detail, num, cur.fetchall())
                if why:
                    cleared.append((num, owner, domain, gist, why))
                continue

            if bclass == "capability":
                # to_regclass is the catalog's own answer and returns null
                # rather than raising on a name that does not exist, so an
                # invented identifier in prose costs one cheap lookup and
                # nothing else.
                def relation_exists(name, _cur=cur):
                    _cur.execute("select to_regclass(%s)", ("public." + name,))
                    row = _cur.fetchone()
                    return row is not None and row[0] is not None

                why = test_capability(detail, relation_exists, credential_present)
                if why:
                    cleared.append((num, owner, domain, gist, why))
                else:
                    unknown += 1
                continue

            unknown += 1

    print(f"BLOCKER REVIEW — {today} — {len(rows)} open loop(s)")
    print(f"  {unblockered} carry NO blocker at all — nothing can ever test these, and")
    print( "      that is the real backlog problem rather than a reporting gap")
    print(f"  {human} wait on a human by definition; not testable here")
    print(f"  {unknown} name a blocker this cannot test from the record")
    if untestable_counterparty:
        print(f"  {untestable_counterparty} wait on a counterparty and were NOT tested:")
        for line in _wrap(render_degradation(unreadable), 68):
            print(f"      {line}")
    print()
    if leaked:
        print(f"⚠︎ THE GATE LEAKS: {len(leaked)} loop(s) raised after the gate went live")
        print( "  carry no blocker, though add-loop refuses that. Something reaches")
        print( "  loop_item by another path — find it, or the pile keeps growing:")
        for num, owner, gist in leaked[:8]:
            print(f"    #{num} [{owner}] {gist[:88]}")
        if len(leaked) > 8:
            print(f"    ... and {len(leaked)-8} more")
        print()
    if not cleared:
        print("Nothing has demonstrably cleared since the last run. That is a real"
              " answer, not an empty one.")
        return 0

    print(f"{len(cleared)} loop(s) are NO LONGER BLOCKED and can be worked now:\n")
    for num, owner, domain, gist, why in cleared:
        print(f"  #{num} [{domain}·{owner}] {gist}")
        print(f"      unblocked: {why}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
