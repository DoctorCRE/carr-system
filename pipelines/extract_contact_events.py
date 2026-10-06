#!/usr/bin/env python3
"""Extract REAL, DATED contact events out of narrative already in the database.

THE PROBLEM THIS SOLVES (capture-problem-2026-08-02.md §4, Option B). The system
records what happened as prose, and every mechanism that consumes it — staleness,
reciprocity, the client graph, delivery scoring — needs events. Production holds
125 `analysis` rows and 77 `note` rows of excellent narrative and exactly ONE
contact row. The touches are in there; they are sentences, not rows.

    "Addendum, 2026-07-30: lease-terms call. Client says..."   <- a real call,
    on a real date, currently queryable by nobody.

WHAT IT DOES. Splits every analysis/note row's summary+detail into sentences,
matches each against a table of COMPLETED contact patterns, and emits a proper
contact activity row (kind from activity_kind where is_contact) attached to the
SAME subject the source row was attached to.

THE STOP RULES, IN CODE (the ORDER 43 lesson: a confidently wrong date is worse
than a missing one):

  * NO DATE IS EVER INFERRED. A sentence must carry an explicit date — ISO
    2026-07-30, "Jul 30, 2026", or 7/30/26. A BARE 7/30 is accepted only when the
    source row itself establishes exactly one year (see year_context): every ISO
    date in that row agrees on a year AND that year matches the row's own
    occurred_at. If the row does not settle the year, the bare date is NOT
    guessed — the event goes to the review list.
  * INTENTIONS ARE NOT TOUCHES. "planning to call", "should reach out", "call
    Monday to confirm", "awaiting", "next step" — excluded, counted, sampled in
    the report. So are negations ("there was no separate 7/20 call", "the LOI
    never went out").
  * FUTURE-DATED EVENTS ARE NOT EVENTS. A touch dated after the record that
    describes it is a plan someone wrote down. Excluded to the review list rather
    than imported.
  * NO ROW IS EVER DELETED OR EDITED. This importer only inserts.

WHAT RIDES ON THE ROW so a human can audit every extraction later:
  summary — the source sentence VERBATIM. Not a paraphrase, not a rewrite.
  detail  — an audit block: source activity id, the pattern that matched, the
            date token as it appeared, and how the year was established.
  source  — 'import_extracted'. Distinct from 'stated' (a human said this just
            now), 'import' (a ledger entry moved wholesale) and
            'import_extracted' (a machine read a sentence and made a claim).
  actor   — the human the SENTENCE names, if it names exactly one; otherwise the
            source row's own actor. Never guessed between two candidates.

IDEMPOTENT by record_source(source_system, external_key), the import_wave1 /
import_md_ledgers pattern and the same UNIQUE constraint. external_key is
`extract:<source_activity_id>:<n>` — stable, human-legible, unique per extraction.
A rerun writes 0 rows.

DRY RUN BY DEFAULT. --apply writes. Run through db-tap so no DSN reaches a shell:

    .venv/bin/python tools/db-tap.py run pipelines/extract_contact_events.py
    .venv/bin/python tools/db-tap.py --branch rehearse-extract run \
        pipelines/extract_contact_events.py --apply --rehearse

--apply against PRODUCTION is the supervisor's tap, not this script's habit.
--rehearse proves it is pointed at a branch rather than trusting the caller.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

import psycopg

SOURCE_SYSTEM = "extract"
SOURCE_LABEL = "import_extracted"
SCAN_KINDS = ("analysis", "note")

FK = {"client": "client_id", "deal": "deal_id", "lead": "lead_id", "vendor": "vendor_id"}

# ---------------------------------------------------------------------------
# DATES
# ---------------------------------------------------------------------------

ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
# "Jul 30, 2026" / "July 30 2026" / "30 July 2026"
MDY_WORD = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(20\d\d))?\b", re.I)
# 7/30/26, 7/30/2026, and the bare 7/30 (year from context ONLY)
SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


def _mk(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(sentence, year_context):
    """-> list of (date, token, year_provenance). NEVER invents a year.

    year_provenance records HOW the year was established, and it travels onto the
    imported row. 'explicit' means the sentence said it. 'row context' means the
    source record settled it and the report says so, so a reader can check.
    """
    out = []
    for m in ISO.finditer(sentence):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            out.append((d, m.group(0), "explicit (ISO)"))
    for m in MDY_WORD.finditer(sentence):
        mo, day, yr = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), m.group(3)
        if yr:
            d = _mk(int(yr), mo, day)
            if d:
                out.append((d, m.group(0), "explicit (month name)"))
        elif year_context:
            d = _mk(year_context, mo, day)
            if d:
                out.append((d, m.group(0), f"row context ({year_context})"))
    for m in SLASH.finditer(sentence):
        mo, day, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        if mo > 12 or day > 31:
            continue                      # not a date: "16/SF", ratios, fractions
        if yr:
            y = int(yr) + 2000 if int(yr) < 100 else int(yr)
            d = _mk(y, mo, day)
            if d:
                out.append((d, m.group(0), "explicit (m/d/y)"))
        elif year_context:
            # THE BARE 7/30. Only reachable when the source row settled one year.
            d = _mk(year_context, mo, day)
            if d:
                out.append((d, m.group(0), f"row context ({year_context})"))
    # stable, dedup by (date, token)
    seen, uniq = set(), []
    for d, tok, prov in out:
        if (d, tok) not in seen:
            seen.add((d, tok))
            uniq.append((d, tok, prov))
    return uniq


def year_context_for(row):
    """The ONE year the source record establishes, or None.

    A bare 7/30 carries no year, so the year has to come from the record around
    it or the event does not get imported. The record establishes it when the
    row's own occurred_at year is not contradicted: every ISO date the row
    carries must agree with that year. A row whose ISO dates straddle years (or
    disagree with its own stamp) settles nothing, returns None, and every bare
    date in it goes to the review list unimported.

    This is the ONLY place a year is supplied from anything but the sentence, it
    is bounded again at use (the resolved date must land in the 365 days before
    the record, never after it), and every row that used it is printed in its own
    section of the report so a human can check the inference rather than trust it.
    """
    text = (row["summary"] or "") + "\n" + (row["detail"] or "")
    years = {int(m.group(1)) for m in ISO.finditer(text)}
    row_year = row["occurred_at"].year
    if years and years != {row_year}:
        return None
    return row_year


# A history array imported wholesale from deals.md: [{'date': ..., 'text': ...}].
# Four such rows carry twelve dated entries between them, and two of the four are
# stored truncated, so this is a REGEX rather than literal_eval — a truncated repr
# must still yield the entries it did finish.
HISTORY_ENTRY = re.compile(
    r"\{'date':\s*'(20\d\d-\d\d-\d\d)',\s*'text':\s*'(.*?)'\s*\}", re.S)


def history_entries(text):
    """-> [(date, text)] for rows that are structured history, else []."""
    if not (text or "").lstrip().startswith("[{'date'"):
        return []
    out = []
    for m in HISTORY_ENTRY.finditer(text):
        d = _mk(*(int(x) for x in m.group(1).split("-")))
        if d:
            out.append((d, m.group(2).replace("\\'", "'")))
    return out


# ---------------------------------------------------------------------------
# CONTACT PATTERNS
# ---------------------------------------------------------------------------
# Each entry: (name, regex, kind or a resolver name). Every pattern is anchored on
# a COMPLETED form. "a call Monday to confirm time" and "planning to call" match
# nothing here; that is the point.

PATTERNS = [
    # --- call -------------------------------------------------------------
    ("called",        r"\bcalled\b(?!\s+(?:it|for|out|off|the\s+shots))", "call"),
    ("phoned",        r"\bphoned\b", "call"),
    ("spoke with",    r"\bspoke\s+(?:with|to)\b", "call"),
    ("had a call",    r"\b(?:had|held|took|joined|ran)\s+(?:a|the|an)?\s*"
                      r"[\w\-/ ]{0,30}?\bcalls?\b", "call"),
    ("on the call",   r"\b(?:on|from|during|per)\s+the\s+[\w\-/ ]{0,30}?\bcalls?\b", "call"),
    ("dated call",    r"\bcall\b(?=[^.]{0,40}\b(?:happened|occurred|took place)\b)", "call"),
    ("addendum call", r"^\s*(?:addendum|update)\b[^\n]{0,60}?\bcalls?\b", "call"),
    ("N call logged", r"\b\d{1,2}/\d{1,2}\s+call\b", "call"),
    ("call is logged", r"\bcalls?\b(?=[^.]{0,60}\bis\s+logged\b)", "call"),

    # --- meeting ----------------------------------------------------------
    ("met with",      r"\bmet\s+(?:with|at)\b", "meeting"),
    ("had a meeting", r"\b(?:had|held|attended|sat\s+(?:down|in))\s+(?:a|the|an)?\s*"
                      r"[\w\-/ ]{0,30}?\bmeetings?\b", "meeting"),
    ("meeting held",  r"\bmeetings?\b(?=[^.]{0,30}\b(?:happened|took place|went|landed)\b)",
                      "meeting"),

    # --- tour -------------------------------------------------------------
    ("toured",        r"\btoured\b", "tour"),
    ("walked",        r"\bwalked\s+(?:the\s+)?(?:space|building|suite|property|site)\b", "tour"),
    ("showing held",  r"\bshowing\b(?=[^.]{0,30}\b(?:happened|took place|went)\b)", "tour"),

    # --- email ------------------------------------------------------------
    ("emailed",       r"\bemailed\b", "@email"),
    # `reply` and `note` were in this alternation and made it fire on
    # "awaiting reply (ETL sent 2026-07-29…)" — where the reply was awaited and
    # what was sent was a different document. A pattern that matches two
    # unrelated clauses is not evidence.
    ("email sent",    r"\b(?:email|e-mail|reply-all)\b[^.]{0,40}\b"
                      r"(?:sent|went\s+out|SENT)\b", "email_out"),
    ("sent email",    r"\bsent\b[^.]{0,30}\b(?:email|e-mail|reply-all)\b", "email_out"),
    ("wrote to",      r"\bwrote\s+(?:to|back\s+to)\b", "email_out"),
    ("replied",       r"\breplied\b", "@email"),

    # --- text -------------------------------------------------------------
    ("texted",        r"\btexted\b", "text"),

    # --- LOI --------------------------------------------------------------
    ("LOI sent",      r"\bLOI\b[^.]{0,60}\b(?:sent|submitted|delivered|went\s+out|"
                      r"SUBMITTED|SENT)\b", "loi"),
    ("sent LOI",      r"\b(?:sent|submitted|delivered)\b[^.]{0,40}\bLOI\b", "loi"),

    # --- counters ---------------------------------------------------------
    ("counter sent",  r"\bcounter(?:ed|-offer|\s+offer)?\b[^.]{0,40}\b"
                      r"(?:sent|submitted|SUBMITTED|SENT|went\s+out|delivered)\b",
                      "counter_sent"),
    ("sent counter",  r"\b(?:sent|submitted|delivered)\b[^.]{0,30}\bcounter\b", "counter_sent"),
    ("counter recvd", r"\bcounter(?:-offer|\s+offer)?\b[^.]{0,40}\b"
                      r"(?:received|came\s+back|returned)\b", "counter_received"),

    # --- introductions ----------------------------------------------------
    # No `intro` kind exists in activity_kind. An intro whose CHANNEL the prose
    # states (an email intro) is email_out on the source's own word. An intro
    # whose channel is unstated is flagged channel_inferred and reported in its
    # own section — never silently filed as though the source named a medium.
    ("email intro",   r"\b(?:email|e-mail|written)\s+intro(?:duction)?\b", "email_out"),
    ("intro emailed", r"\bintro(?:duction)?\b[^.]{0,30}\bemailed\b", "email_out"),
    ("intro sent",    r"\bintro(?:duction)?s?\b[^.]{0,40}\b(?:sent|SENT|made|went\s+out)\b",
                      "@intro"),
    ("introduced",    r"\bintroduced\b(?!\s+himself)", "@intro"),

    # --- lease ------------------------------------------------------------
    ("lease signed",  r"\bleases?\b[^.]{0,40}\b(?:signed|executed|fully\s+executed)\b",
                      "lease_signed"),
    ("signed lease",  r"\b(?:signed|executed)\b[^.]{0,30}\blease\b", "lease_signed"),
]

COMPILED = [(name, re.compile(rx, re.I), kind) for name, rx, kind in PATTERNS]

# --- exclusions ------------------------------------------------------------
# INTENTIONS, not touches. Every PATTERN above is already a completed form, so
# this list holds only the constructions that turn a completed-looking phrase
# into a plan. Words that merely describe the AFTERMATH of a real touch
# ("awaiting reply", "no response received", "pending") are deliberately NOT
# here: "comparison sheet + LOI ask sent 7/29, awaiting reply" is a touch that
# happened followed by a state, and excluding it would throw away the event to
# avoid a word.
#
# `will` is scoped to specific verbs on purpose. A bare \bwill\s+\w+ silently
# eats "William \"Will\" Carlson, MD" — a client's partner, named in the C-126
# dossier — and with him the real 7/22 email that names him.
INTENT = [
    ("plan/modal", re.compile(
        r"\b(?:plan(?:s|ned|ning)?\s+to|going\s+to|about\s+to|intend(?:s|ed)?\s+to|"
        r"needs?\s+to|should\s+\w+|"
        r"will\s+(?:call|email|meet|tour|send|reach|schedule|follow|introduce)|"
        r"to\s+be\s+(?:called|emailed|toured|scheduled))\b", re.I)),
    ("scheduling/future", re.compile(
        r"\b(?:to\s+schedule|scheduling|schedule\s+a|upcoming|"
        r"next\s+steps?\b|follow-?up\s+(?:call|meeting|email)\b|"
        r"still\s+to\s+(?:be\s+)?(?:tour|toured|call|email)|"
        r"reach\s+out|when\s+a\s+meeting\s+lands|after\s+a\s+meeting\s+lands|"
        r"if\s+(?:he|she|they)\b|possibly\b|"
        r"proposed\b|queued\b|confirm\s+time|"
        r"by\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday)\b)", re.I)),
    ("hypothetical/rule", re.compile(
        r"\b(?:the\s+standard\s+is|standard\s+follow-?up|template\b|"
        r"doctrine\b|the\s+rule\b|policy\b|any\s+vendor\s+intro)\b", re.I)),
]

# A CHANGE-LOG STAMP. "Last updated: 2026-07-29 (… LOI ask sent to Erik + Will.)"
# dates the RECORD, and the events listed in its parenthetical may have happened
# any time before it. That is precisely the ambiguity the stop rule covers, so
# these go to the review list with the reason stated rather than borrowing the
# stamp's date for an event. Real events usually appear a second time in the body
# with their own date; where they do not, the review list is where a human sees it.
RECORD_STAMP = re.compile(
    r"^\s*[-*>\s]*(?:Last\s+updated|Prior|Updated|Logged|Recorded|Verified|Source)\s*[:—-]",
    re.I)

# A DATE ON THE THING BEING ANSWERED, not on the answer. "She replied to the
# June 30 email" states when the ORIGINAL went out; the reply came later, by an
# unstated amount. Importing 06-30 as the reply's date would be the ORDER 43
# error with a plausible face on it, so it goes to review instead.
REPLIED_TO_DATED = re.compile(
    r"\b(?:repl(?:y|ied|ies)|respond(?:ed|s)?|answered|following\s+up)\b[^.]{0,25}"
    r"\bto\b[^.]{0,25}(?:20\d\d-\d\d-\d\d|\d{1,2}/\d{1,2}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})", re.I)
NEGATION = re.compile(
    r"\b(?:no\s+(?:separate\s+)?(?:call|meeting|tour|email|reply|contact|record|touch)|"
    r"never\s+(?:went|sent|met|called|happened|toured|made)|"
    r"has\s+never\s+met|had\s+not\s+(?:yet\s+)?(?:met|called|been)|"
    r"did\s+not\s+(?:call|meet|email|tour|send)|didn'?t\s+(?:call|meet|email|tour|send)|"
    r"was\s+not\s+\w+|wasn'?t\s+\w+|superseded|cancell?ed\b|moot\b|"
    r"instead\s+of\s+a\s+separate|rather\s+than\s+a\s+separate|"
    r"there\s+was\s+no\b|nobody\s+\w+|no\s+one\s+\w+)", re.I)
# "call" as a noun meaning a DECISION, not a phone call.
CALL_NOUN = re.compile(
    r"(?:\b(?:Joe|Dell|Dale|his|her|their|my|your|our|judgment|judgement|close|tough|"
    r"right|wrong|final|the\s+agent)'?s?\s+call\b)|\bcalled\s+it\b|\bas\s+\w+\s+called\b|"
    r"\bcalled\s+[\w\s]{1,20}[\"\u201c\u2018']|"
    r"\bso-called\b|\bon-?call\b|\bcall\s+it\b", re.I)

# WHO. The sentence names an actor only if it names exactly one.
# "Dale" is Dell's own alias with clients — his dossiers say so explicitly.
ACTOR_TOKENS = {"joe": "joe", "dell": "dell", "dale": "dell"}
ACTOR_RX = re.compile(r"\b(Joe|Dell|Dale)\b", re.I)
# First person / house = outbound from us.
OUTBOUND_SELF = re.compile(r"\b(?:Joe|Dell|Dale|we|I|us|our|CARR|CARR'?s)\b", re.I)

ABBREV = re.compile(r"\b(?:Dr|Mr|Mrs|Ms|Ste|St|Inc|Co|Corp|Jr|Sr|approx|vs|No|Ave|Blvd|"
                    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.$", re.I)


def sentences(text):
    """Split prose into auditable sentences. Markdown bullets and headings are
    their own sentences; `Dr.` and friends do not end one."""
    out = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts, buf = [], ""
        # The split allows markdown emphasis to sit BETWEEN the full stop and the
        # space. Without it "…still to be toured.** Joe **emailed Whitfield today
        # (7/21)…" stays one sentence, and the intention in the first half
        # suppresses the real send in the second — the exact way a filter starts
        # eating events it was never aimed at.
        for tok in re.split(r"(?<=[.!?])[*_~`]*\s+", line):
            buf = (buf + " " + tok).strip() if buf else tok
            if ABBREV.search(buf):
                continue
            parts.append(buf)
            buf = ""
        if buf:
            parts.append(buf)
        out.extend(p for p in parts if p)
    return out


def clean(sentence):
    """Markdown stripped for MATCHING only. The stored summary stays verbatim."""
    s = re.sub(r"~~([^~]*)~~", r"\1", sentence)
    s = re.sub(r"[*_`>#|]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_email_direction(text, match_start):
    """email_out / email_in / None. Reads the SUBJECT of the verb.

    Three readings, in order of how much the prose actually says:
      * we are the subject ("Joe emailed…", "Dell emailed…", "we wrote") -> out
      * a named third party is the subject ("Erik emailed 7/22") -> in
      * NO subject at all, the dossier's own shorthand under a label
        ("**Outreach notes:** Emailed Sorbara 1/16/26") -> out, because the record
        is written from CARR's side and an unattributed send is CARR's send
    None is returned when none of the three reads cleanly, and the sentence goes
    to the review list rather than being assigned a direction it did not state.
    """
    before = text[max(0, match_start - 70):match_start]
    if OUTBOUND_SELF.search(before):
        return "email_out"
    # A third party as the subject: a proper noun immediately before the verb.
    if re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*$", before):
        return "email_in"
    # Nothing before the verb but a label, a bullet, or the start of the sentence.
    if re.fullmatch(r"[\s\-*>|]*(?:[A-Za-z ]{0,24}:)?[\s\-*>|]*", before):
        return "email_out"
    if OUTBOUND_SELF.search(text):
        return "email_out"
    return None


def resolve_actor(text, row_actor):
    names = {ACTOR_TOKENS[m.group(1).lower()] for m in ACTOR_RX.finditer(text)}
    if len(names) == 1:
        return names.pop(), "named in sentence"
    return row_actor, "inherited from source row"


def subject_of(row):
    for st, col in FK.items():
        if row.get(col):
            return st, row[col]
    return None, None


# ---------------------------------------------------------------------------

def units(row):
    """-> [(sentence, fixed_date or None, unit_label)].

    Most rows are prose and yield sentences with no date of their own. The four
    history-array rows imported from deals.md are STRUCTURED — each entry carries
    its own `date` field — so their entries are handed down with that date fixed.
    That is not an inference: the source states the date as a field.
    """
    text = (row["summary"] or "") + "\n" + (row["detail"] or "")
    hist = history_entries(row["summary"] or "")
    if hist:
        out = []
        for d, body in hist:
            for s in sentences(body):
                out.append((s, d, "history entry"))
        return out
    return [(s, None, "prose") for s in sentences(text)]


def extract(rows):
    """-> (events, review, excluded). Pure; no database, so it is testable."""
    events, review, excluded = [], [], []

    for row in rows:
        st, sid = subject_of(row)
        yc = year_context_for(row)
        ordinal = 0

        for raw, fixed_date, unit in units(row):
            s = clean(raw)
            hits = []
            for name, rx, kind in COMPILED:
                m = rx.search(s)
                if m:
                    hits.append((name, m, kind))
            if not hits:
                continue

            name, m, kind = hits[0]
            base = {"src_id": row["id"], "src_kind": row["kind"],
                    "src_occurred": row["occurred_at"].date(),
                    "subject_type": st, "subject_id": sid,
                    "subject_ref": row.get("ref"), "subject_name": row.get("display_name"),
                    "sentence": raw.strip(), "pattern": name, "unit": unit}

            # ---- exclusions, in the order that makes the report readable ----
            if kind == "call" and CALL_NOUN.search(s) and name in (
                    "called", "had a call", "on the call", "dated call"):
                excluded.append({**base, "reason": "'call' here is a decision, not a phone call"})
                continue
            neg = NEGATION.search(s)
            if neg:
                excluded.append({**base, "reason": f"negated / did-not-happen ({neg.group(0)!r})"})
                continue
            # INTENT IS POSITIONAL, negation is not, and the asymmetry is
            # deliberate. In English an intent marker governs the verb that
            # FOLLOWS it ("plans to email"), so one sitting after the verb is
            # describing what comes next, not cancelling what happened: "Joe
            # emailed Whitfield today (7/21) to schedule a call" is a send that
            # happened, and the first pass threw it away. Negation stays
            # whole-sentence, because "the drafted LOI never went out" puts the
            # negation after the noun the pattern anchored on and a positional
            # rule there would import an LOI that was never sent.
            intent = next(((lbl, mm) for lbl, rx in INTENT
                           for mm in [rx.search(s)] if mm and mm.start() < m.start()), None)
            if intent:
                excluded.append({**base, "reason": f"intention, not a touch — {intent[0]} "
                                                   f"({intent[1].group(0)!r})"})
                continue

            # ---- channel / direction ----
            note = None
            if kind == "@email":
                d = resolve_email_direction(s, m.start())
                if d is None:
                    review.append({**base, "reason": "email with no determinable direction "
                                                     "(who wrote to whom is not stated)"})
                    continue
                kind = d
            elif kind == "@intro":
                kind = "email_out"
                note = ("channel not stated in the source; an intro that was SENT is "
                        "recorded as email_out — verify the medium before relying on it")

            # ---- the date. Everything above is worthless without this. ----
            if fixed_date is not None:
                d, tok, prov = fixed_date, str(fixed_date), "history entry `date` field"
            else:
                if REPLIED_TO_DATED.search(s):
                    review.append({**base, "kind": kind,
                                   "reason": "the date here belongs to the message being "
                                             "replied to, not to the reply — the reply's own "
                                             "date is not stated"})
                    continue
                if RECORD_STAMP.match(raw):
                    review.append({**base, "kind": kind,
                                   "reason": "the date on this line is the RECORD's update "
                                             "stamp, not the event's — borrowing it would "
                                             "date the touch by when someone wrote it down"})
                    continue
                found = find_dates(s, yc)
                if not found:
                    bare = SLASH.search(s) or MDY_WORD.search(s)
                    why = ("a bare date is present but the source record does not establish "
                           "one year — NOT guessed") if bare \
                          else "no explicit date in the sentence"
                    review.append({**base, "kind": kind, "reason": why})
                    continue
                distinct = sorted({t[0] for t in found})
                if len(distinct) > 1:
                    # Several DIFFERENT dates in one sentence is exactly where a
                    # wrong date gets born. "Earliest wins" would be a guess with
                    # a rule painted on it. (2026-07-30 written twice, once ISO
                    # and once as 7/30, is one date and passes.)
                    review.append({**base, "kind": kind,
                                   "reason": f"{len(distinct)} different dates in one sentence "
                                             f"({', '.join(str(x) for x in distinct)}) — which "
                                             f"one the event happened on is not determinable"})
                    continue
                d, tok, prov = found[0]
                if prov.startswith("row context") and (row["occurred_at"].date() - d).days > 365:
                    # The year came from the record, so it only holds near the
                    # record. A bare 1/16 in a July file could be either January.
                    review.append({**base, "kind": kind,
                                   "reason": f"bare date `{tok}` would resolve to {d}, more "
                                             f"than a year before the record "
                                             f"({row['occurred_at'].date()}) — the year is "
                                             f"not established, NOT guessed"})
                    continue
            if d > row["occurred_at"].date():
                review.append({**base, "kind": kind, "date": d,
                               "reason": f"dated {d}, AFTER the record that describes it "
                                         f"({row['occurred_at'].date()}) — a plan, not a touch"})
                continue

            actor, actor_prov = resolve_actor(s, row["actor"])
            ordinal += 1
            events.append({**base, "kind": kind, "date": d, "date_token": tok,
                           "date_prov": prov, "actor": actor, "actor_prov": actor_prov,
                           "ordinal": ordinal, "note": note})
    return events, review, excluded


FIELD_LABEL = re.compile(r"^\s*[-*>|\s]*\**\s*(?:Status|Next step|Lead source|Outreach notes|"
                         r"Key angle|Target|Situation)\b\s*:", re.I)

# Patterns that only REFERENCE a touch ("on the 7/29 call…") rather than narrate
# it ("Joe called a vendor…"). Both are valid evidence that the touch happened;
# the narrated one is the better sentence to keep as the audit record.
WEAK_PATTERNS = {"on the call", "dated call", "N call logged", "call is logged",
                 "meeting held", "showing held"}


STOPWORDS = set("a an the and or but of to in on at for with by from is are was were "
                "be been it its this that these those he she they him her them his "
                "their our we us i as not no so if then than which who whom what when "
                "same day about into over under after before during".split())


def content_words(sentence):
    words = re.findall(r"[a-z0-9$/][a-z0-9$/'-]+", clean(sentence).lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


OVERLAP_SAME_TOUCH = 0.65


def overlap(a, b):
    """Overlap coefficient — |A∩B| / min(|A|,|B|) — NOT Jaccard.

    The pairs being compared are routinely a one-line restatement against a long
    narrative sentence ("Dell introduced him to Sam 7/14." against "Dell
    introduced Sam to a banker contact (V-BNK-017, a national bank) on 2026-07-14,
    which opened the current thread."). Jaccard scores that pair 0.23 purely for
    length, and it is plainly one introduction. Overlap scores it 0.75.
    (Example sanitized 2026-08-06, ORDER 42b — the original named a real vendor.)
    """
    wa, wb = content_words(a["sentence"]), content_words(b["sentence"])
    if not wa or not wb:
        return 1.0
    return len(wa & wb) / min(len(wa), len(wb))


def same_touch(a, b):
    """Two extractions on one subject, one kind, one date — the same touch, or two?

    Two tests, because two different things produce a collision.

    A REFERENCE never creates an event. "The 7/28 call is logged as a worked
    recovery example", "Dell was on the 7/28 call", "Built from the 7/28 call
    transcript" are three sentences about ONE call, sharing almost no words. Word
    overlap cannot tell them apart and does not have to: a weak-pattern match is
    by construction a mention of a touch, not a narration of one, so it folds into
    whatever it collides with.

    Two NARRATED touches on one day are decided on overlap. C-112 on 2026-07-30
    has both "Addendum, 2026-07-30: lease-terms call" (the client) and "Joe called
    the listing agent 7/30" (the other side) at overlap 0.00 — two real calls, and
    collapsing them would delete a touch to avoid a duplicate that was not there.
    C-155's three 2026-07-29 emails are really two, and the restatements score
    1.00 against their originals.
    """
    if a["pattern"] in WEAK_PATTERNS or b["pattern"] in WEAK_PATTERNS:
        return True
    return overlap(a, b) >= OVERLAP_SAME_TOUCH


def dedupe(events):
    """The same touch is narrated in several rows (a dossier header repeats its own
    addenda). Collapse on (subject, kind, date) so touch counts are not inflated.

    WHICH copy survives matters, because the surviving sentence IS the audit
    record. A narrative sentence ("Addendum, 2026-07-30: lease-terms call") is a
    better artifact than the same fact restated inside a status field, so field
    labels sort last. Beyond that the order is source id then ordinal: fixed, so
    reruns agree and the external keys stay stable."""
    events = sorted(events, key=lambda e: (bool(FIELD_LABEL.match(e["sentence"])),
                                           e["pattern"] in WEAK_PATTERNS,
                                           str(e["src_id"]), e["ordinal"]))
    kept, dropped, seen = [], [], defaultdict(list)
    for e in events:
        key = (e["subject_type"], str(e["subject_id"]), e["kind"], e["date"])
        # Best match, not first match: within one group a sentence can collide
        # with several kept events, and it belongs to the one it matches hardest.
        cands = [k for k in seen[key] if same_touch(k, e)]
        twin = max(cands, key=lambda k: overlap(k, e)) if cands else None
        if twin:
            dropped.append({**e, "duplicate_of": twin["src_id"],
                            "duplicate_sentence": twin["sentence"]})
        else:
            seen[key].append(e)
            kept.append(e)
    return kept, dropped


def _is_production(url):
    """Ask Neon, never the caller. A flag that only promises to be safe is not a
    safety mechanism. (Same check as import_dossier_analysis.)"""
    import subprocess
    from urllib.parse import urlparse
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    neonctl = os.path.join(repo, "mcp-server", "node_modules", ".bin", "neonctl")
    out = subprocess.run(
        [neonctl, "connection-string", "production", "--project-id",
         "steep-field-48688294", "--role-name", "neondb_owner"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:"
             + os.environ.get("PATH", "")})
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("could not resolve production's DSN to compare against — refusing to "
                 "guess whether this is production")
    return urlparse(url).hostname == urlparse(out.stdout.strip()).hostname


def subjects_with_contact(cur):
    """Every subject that already has at least one is_contact activity row. The
    report's headline number — how many subjects GAIN a last_touch — is this set
    subtracted from the extraction's, so it has to be read before any write."""
    cur.execute("""
        select subject_type, subject_id::text from (
          select 'client' t, client_id i from activity a join activity_kind k on k.slug=a.kind
           where k.is_contact and a.client_id is not null
          union all
          select 'deal', deal_id from activity a join activity_kind k on k.slug=a.kind
           where k.is_contact and a.deal_id is not null
          union all
          select 'lead', lead_id from activity a join activity_kind k on k.slug=a.kind
           where k.is_contact and a.lead_id is not null
          union all
          select 'vendor', vendor_id from activity a join activity_kind k on k.slug=a.kind
           where k.is_contact and a.vendor_id is not null
        ) x (subject_type, subject_id)
    """)
    return {(t, i) for t, i in cur.fetchall()}


def load_rows(cur):
    cur.execute("""
        select a.id, a.kind, a.occurred_at, a.summary, a.detail, a.source,
               a.client_id, a.deal_id, a.lead_id, a.vendor_id, act.slug as actor
          from activity a join actor act on act.id = a.actor_id
         where a.kind = any(%s)
         order by a.occurred_at, a.id
    """, (list(SCAN_KINDS),))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def label_subjects(cur, rows):
    """Ref + display name per subject, for a report a human can actually read.
    Missing labels are cosmetic only — never a reason to import or not import."""
    try:
        cur.execute("select subject_type, subject_id, ref, display_name from v_ref_index")
        idx = {(st, str(sid)): (ref, nm) for st, sid, ref, nm in cur.fetchall()}
    except psycopg.Error:
        return
    for r in rows:
        st, sid = subject_of(r)
        if st:
            ref, nm = idx.get((st, str(sid)), (None, None))
            r["ref"], r["display_name"] = ref, nm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the rows. Without it this is a DRY RUN.")
    ap.add_argument("--dry-run", action="store_true", help="explicit no-op; dry run is default")
    ap.add_argument("--rehearse", action="store_true",
                    help="permit --apply against a NEON BRANCH only; refuses production")
    ap.add_argument("--samples", type=int, default=10,
                    help="how many full extractions to print for human judgement")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("no DATABASE_URL — run through tools/db-tap.py")
    if a.apply and a.rehearse and _is_production(url):
        sys.exit("--rehearse was pointed at PRODUCTION. Refusing. Use "
                 "tools/db-tap.py --branch <name>.")

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # GATE. The contact vocabulary must exist before anything claims to write
        # into it, and it must still say these kinds are contact.
        cur.execute("select slug from activity_kind where is_contact")
        contact_kinds = {r[0] for r in cur.fetchall()}
        need = {"call", "meeting", "tour", "email_out", "email_in", "text",
                "loi", "counter_sent", "counter_received", "lease_signed"}
        missing = need - contact_kinds
        if missing:
            sys.exit(f"activity_kind is missing contact kinds {sorted(missing)} — STOP.")

        rows = load_rows(cur)
        label_subjects(cur, rows)
        cur.execute("select slug, id from actor")
        actors = dict(cur.fetchall())

        events, review, excluded = extract(rows)
        events, dupes = dedupe(events)

        # Refuse on an unresolvable subject rather than orphan a contact row.
        orphans = [e for e in events if not e["subject_type"]]
        if orphans:
            sys.exit(f"REFUSING: {len(orphans)} extraction(s) come from activity rows with no "
                     f"subject FK (first: {orphans[0]['src_id']}). Nothing imported.")
        bad_actor = {e["actor"] for e in events} - set(actors)
        if bad_actor:
            sys.exit(f"REFUSING: extraction names actor(s) {sorted(bad_actor)} that do not "
                     "exist. Author is never guessed. Nothing imported.")

        # WHICH SUBJECTS ALREADY HAVE A TOUCH — read BEFORE the write, or the
        # answer is always zero: --apply would insert the very rows the baseline
        # is supposed to predate, and the report would say the import changed
        # nothing on the one number it exists to show.
        had_touch = subjects_with_contact(cur)

        # ---- write ----
        inserted = skipped = 0
        if a.apply:
            system = actors["system"]
            for e in events:
                ext_key = f"extract:{e['src_id']}:{e['ordinal']}"
                cur.execute("select entity_id from record_source "
                            "where source_system=%s and external_key=%s",
                            (SOURCE_SYSTEM, ext_key))
                if cur.fetchone():
                    skipped += 1
                    continue
                detail = (
                    f"[extracted from activity {e['src_id']} ({e['src_kind']}) by "
                    f"pipelines/extract_contact_events.py]\n"
                    f"source sentence: {e['sentence']}\n"
                    f"matched pattern: {e['pattern']}\n"
                    f"date token: {e['date_token']} · year {e['date_prov']}\n"
                    f"actor: {e['actor']} ({e['actor_prov']})")
                if e.get("note"):
                    detail += f"\nNOTE: {e['note']}"
                cur.execute(
                    f"insert into activity (occurred_at, actor_id, kind, summary, detail, "
                    f"{FK[e['subject_type']]}, source) "
                    "values (%s::date, %s, %s, %s, %s, %s, %s) returning id",
                    (e["date"], actors[e["actor"]], e["kind"], e["sentence"], detail,
                     e["subject_id"], SOURCE_LABEL))
                act_id = cur.fetchone()[0]
                cur.execute(
                    "insert into record_source (entity_type, entity_id, source_system, "
                    "external_key, imported_at) values ('activity', %s, %s, %s, now())",
                    (act_id, SOURCE_SYSTEM, ext_key))
                # The extraction is the SYSTEM's act; the touch's actor is on the row.
                cur.execute(
                    "insert into event (occurred_at, actor_id, verb, subject_type, subject_id, "
                    "new_value, cause) values (now(), %s, 'import', %s, %s, %s::jsonb, "
                    "'import_migration')",
                    (system, e["subject_type"], e["subject_id"],
                     json.dumps({"activity": str(act_id), "external_key": ext_key,
                                 "extracted_from": str(e["src_id"]), "kind": e["kind"],
                                 "actor": e["actor"]})))
                inserted += 1
            conn.commit()

    report(a, rows, events, review, excluded, dupes, had_touch, inserted, skipped)


def report(a, rows, events, review, excluded, dupes, had_touch, inserted, skipped):
    by_kind = Counter(e["kind"] for e in events)
    subjects = defaultdict(list)
    for e in events:
        subjects[(e["subject_type"], str(e["subject_id"]))].append(e)
    gained = {k: v for k, v in subjects.items() if k not in had_touch}

    L = []
    w = L.append
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    w("# Contact-event extraction — capture fix, Option B")
    w("")
    w(f"*{'APPLIED' if a.apply else 'DRY RUN — nothing written'} · {stamp} · "
      f"pipelines/extract_contact_events.py*")
    w("")
    w("## Numbers")
    w("")
    w(f"- rows scanned: **{len(rows)}** ({Counter(r['kind'] for r in rows)})")
    w(f"- events extracted: **{len(events)}**"
      + (f" (+{len(dupes)} collapsed as the same touch narrated twice)" if dupes else ""))
    w(f"- by kind: " + ", ".join(f"**{k}** {v}" for k, v in by_kind.most_common()))
    w(f"- subjects touched by an extracted event: **{len(subjects)}**; of those, "
      f"**{len(gained)}** had NO contact row before and gain a last_touch")
    w(f"- review list (contact verb, no usable date): **{len(review)}**")
    w(f"- excluded as intentions / negations / non-events: **{len(excluded)}**")
    if a.apply:
        w(f"- inserted: **{inserted}** · already present (idempotent skip): **{skipped}**")
    w("")

    w("## Every extraction")
    w("")
    w("| # | source row | subject | kind | date | matched | source sentence |")
    w("|---|---|---|---|---|---|---|")
    for i, e in enumerate(sorted(events, key=lambda x: (x["date"], x["kind"])), 1):
        sub = e["subject_ref"] or e["subject_type"]
        nm = f" {e['subject_name']}" if e.get("subject_name") else ""
        sent = e["sentence"].replace("|", "\\|").replace("\n", " ")
        w(f"| {i} | `{str(e['src_id'])[:8]}` | {sub}{nm} | **{e['kind']}** | {e['date']} "
          f"| {e['pattern']} | {sent[:300]} |")
    w("")

    flagged = [e for e in events if e.get("note")]
    if flagged:
        w("## Channel inferred — read these before trusting the kind")
        w("")
        for e in flagged:
            w(f"- `{str(e['src_id'])[:8]}` {e['date']} **{e['kind']}** — {e['sentence'][:220]}")
            w(f"  - {e['note']}")
        w("")

    ctx = [e for e in events if e["date_prov"].startswith("row context")]
    if ctx:
        w("## Year taken from the source record (bare m/d), not from the sentence")
        w("")
        for e in ctx:
            w(f"- `{str(e['src_id'])[:8]}` token `{e['date_token']}` -> {e['date']} "
              f"({e['date_prov']}) — {e['sentence'][:180]}")
        w("")

    w("## Review list — a contact verb with no importable date. NOT imported.")
    w("")
    for r in sorted(review, key=lambda x: x["reason"]):
        sub = r.get("subject_ref") or r["subject_type"]
        w(f"- `{str(r['src_id'])[:8]}` {sub} · {r.get('kind', '?')} — {r['reason']}")
        w(f"  > {r['sentence'][:260]}")
    w("")

    w("## Excluded — intentions, negations, and 'call' meaning a decision")
    w("")
    for reason, n in Counter(x["reason"].split(" (")[0] for x in excluded).most_common():
        w(f"- {n} × {reason}")
    w("")
    w("<details><summary>every excluded sentence</summary>")
    w("")
    for x in sorted(excluded, key=lambda y: y["reason"]):
        w(f"- {x['reason']}")
        w(f"  > {x['sentence'][:220]}")
    w("")
    w("</details>")
    w("")

    if dupes:
        w("## Collapsed duplicates — the same touch narrated in more than one row")
        w("")
        w("Collapsed on (subject, kind, date) when the two sentences describe one touch: "
          "a reference to a touch folds into the narration of it, and two narrations fold "
          "together above 0.65 word overlap. Two narrations BELOW that are kept as two "
          "events, because C-112 really did have a client call and a listing-agent call on "
          "2026-07-30. KNOWN RESIDUAL: the C-125 warm intro of 2026-07-13 is stated in two "
          "dossier fields that score 0.57–0.68 and it survives as two rows. Check it here "
          "rather than trusting the count.")
        w("")
        for d in dupes:
            w(f"- `{str(d['src_id'])[:8]}` {d['date']} {d['kind']} — same "
              f"(subject, kind, date) as `{str(d['duplicate_of'])[:8]}`")
            w(f"  > {d['sentence'][:200]}")
        w("")

    os.makedirs("out", exist_ok=True)
    path = os.path.join("out", f"contact-extraction-{stamp}.md")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")

    # terminal: the numbers plus N full samples for human judgement
    print("\n".join(L[:16]))
    print(f"\n---- {a.samples} sample extractions, verbatim ----")
    for e in sorted(events, key=lambda x: (x["date"], x["kind"]))[:a.samples]:
        print(f"\nsource activity {e['src_id']} ({e['src_kind']})")
        print(f"  sentence : {e['sentence'][:400]}")
        print(f"  -> kind {e['kind']} · occurred_at {e['date']} ({e['date_prov']}) "
              f"· actor {e['actor']} ({e['actor_prov']})")
        print(f"  -> subject {e['subject_ref'] or e['subject_type']} "
              f"{e.get('subject_name') or ''} · source {SOURCE_LABEL} "
              f"· key extract:{e['src_id']}:{e['ordinal']}")
        if e.get("note"):
            print(f"  -> NOTE {e['note']}")
    print(f"\nreport: {path}")
    if not a.apply:
        print("DRY RUN — nothing written.")


if __name__ == "__main__":
    main()
