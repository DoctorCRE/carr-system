#!/usr/bin/env python3
"""store-markup-scan.py — hunt tool-call markup across EVERY text column in the
record layer, not just the rule table.

WHY THIS EXISTS. On 2026-08-13 six ACTIVE rules were found carrying literal
tool-call markup: a malformed parameter made the statement swallow its own
closing tag plus every parameter after it, and those parameters were written
NULL. Rule c53beeaa documents the defect and prescribes the check; nothing ran
it, and the corruption sat in binding text for four days.

The rules were then covered by ops/rule-render-markup-check.py. But the same
write path serves every verb that takes prose — deal notes, findings, loop
bodies, decision rationales, client records, doctrine sections. c53beeaa names
those explicitly ("a decision's rationale absorbed into human_quote, and loop
#146's and #159's unblocks and source_note into body"), so the record side was
known to be affected and had never been swept.

READ-ONLY BY CONSTRUCTION: connects with the exporter role via
exporters/common.connect(). It finds and reports; it repairs nothing, because
the repair needs the absorbed text read first and each parameter returned to its
own field, which is a judgment call per row.

OUTPUT IS DELIBERATELY REDACTED. It prints table, column, primary key and a
short window around the marker — enough to locate and triage a row, without
dumping client prose into a transcript.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exporters.common import connect  # noqa: E402

MARKERS = ["<parameter", "</parameter", "<invoke", "</invoke"]
WINDOW = 70

# The record of what happened must keep the junk: rule c53beeaa is explicit that
# `event` and `tool_call` store the pre-amend text on purpose so a change stays
# reversible, and that "a sweep that 'cleans' those is falsifying the record,
# not repairing it." They are scanned but reported separately and never counted
# as damage.
AUDIT_TABLES = {"event", "tool_call"}

# Tables whose whole job is to DESCRIBE failures will quote failure signatures in
# their narrative columns — the defect row filed about this very defect names the
# four marker strings in prose. Same carve-out, and same reasoning, as the rule
# that documents the check being allowed to quote it.
DOC_COLUMNS = {("defect", "claimed"), ("defect", "actual"),
               ("defect", "source_unread"), ("defect", "cost_note")}

# A row the system REFUSES to let anyone repair must not hold a nightly check
# red forever. update-loop answers `loop_not_open` on a closed loop — "a closed
# loop is history; open a new one rather than editing the record of what
# happened" — so corruption in a done row is permanent BY DESIGN, and no amount
# of reporting will change it. Proven on loop #159 (2026-08-13): the repair was
# extracted correctly and the verb declined it. Those rows are still listed,
# because silently dropping them would misreport the store as clean, but they do
# not fail the run. The check exists to catch NEW damage in live records.
HISTORICAL_SQL = {
    "loop_item": "status <> 'open'",
}

# JOE'S RULING, 2026-08-13: "accept them permanently as history."
#
# These nine closed rows are corrupt and stay corrupt. That is the settled end
# state, not a deferral, so they are reported as a single accepted count instead
# of eighteen lines every night — a nightly report that lists the same
# unactionable rows forever trains the reader to skip the section, which is how
# the check this defect needed came to sit unrun for ten days in the first place.
# Pass --verbose to see them.
#
# ACCEPTED IS A FIXED SET, NOT A BLANKET AMNESTY FOR ANYTHING CLOSED. A row
# corrupted tomorrow and closed before anyone noticed would otherwise inherit the
# ruling and vanish. Any historical hit whose row is NOT in this set fails the
# run, because it is new damage wearing history's clothes.
ACCEPTED_HISTORICAL = {
    "52250c55-0abe-4b0b-8a95-b3f7d7c9ffe8",  # loop #159, absorbed unblocks/source_note/since
    "3bd353f2-8092-4674-b8b1-8ec4bd4f70d2",  # loop #248
    "07118214-6c41-4850-baac-b91e6e93b8bb",  # loop #316
    "c053920e-9e7c-4eca-928d-50a79340e955",  # loop #252
    "68520b7e-74a0-42f8-9d12-67bb68161a52",  # loop #276
    "0c0f5557-7090-47d8-9de8-5c0cdd59e50f",  # loop #239
    "c17997bd-e442-4a6b-94e6-aac5ecbc5a78",  # idea #66
    "adbe90af-676f-4bc3-9619-27d3fc002045",  # idea #67
    "3b152875-25fb-4916-af63-60a46f042336",  # loop #342
    # loop #374, the push-safety loop ("do not push from the working checkout
    # until the pre-push fixture is restored"). Corrupted and closed 2026-08-14,
    # AFTER the ruling above, so it does not inherit it and was not meant to —
    # it is added here as its own decision, with the evidence, because that is
    # what the paragraph above asks for.
    #
    # Ruled 2026-08-14 on Joe's "fix this", after establishing three things:
    #   1. Nothing was lost. source_note swallowed </source_note> and the whole
    #      unblocks field after it, but the absorbed text is still readable in
    #      source_note, and both fields are transcribed verbatim into loop #379.
    #   2. It cannot be repaired. update-loop refuses a closed row on purpose —
    #      "a closed loop is history; open a new one rather than editing the
    #      record of what happened" — and that refusal is the design working.
    #   3. The loop was closed correctly on its merits at 05:57; the corruption
    #      is cosmetic and does not touch what the record says happened.
    # The cause is now closed at the write door (looksLikeToolCallMarkup in
    # mcp-server/src/tools.js), so this set should stop growing rather than
    # becoming the routine landing place for new damage. If it grows again, the
    # guard is not holding and that is the thing to fix, not this list.
    "c8da74bd-49ef-40b8-baa3-fca2f6bb64e3",  # loop #374
}


def classify(column, val):
    """CORRUPTION vs MENTION.

    A row that WRITES ABOUT this defect legitimately contains the marker strings
    — the rule that documents it, the loops that tracked the cleanup, the defect
    filed about it. A scan that cannot tell those from actual damage cries wolf
    on its own paper trail, and a noisy check gets ignored, which is how the
    original one came to never run at all.

    The signature of real corruption is structural, not lexical: the field
    swallowed ITS OWN closing tag (`</body>` in body, `</outcome>` in outcome),
    or it carries a bare `<parameter name=` that ate the field which should have
    followed. A mention has the markers quoted inside backticks as prose.
    """
    own_closer = f"</{column.replace('close_', '')}>"
    if own_closer in val:
        return "CORRUPTION"
    for m in MARKERS:
        idx = val.find(m)
        while idx != -1:
            # Quoted as prose inside backticks is discussion, not damage.
            before = val.rfind("`", 0, idx)
            after = val.find("`", idx)
            quoted = before != -1 and after != -1 and "\n" not in val[before:after]
            if not quoted:
                return "CORRUPTION"
            idx = val.find(m, idx + 1)
    return "MENTION"


def main() -> int:
    conn = connect()
    cur = conn.cursor()

    # BASE TABLES ONLY. Views are derived: v_loops and v_subject_timeline each
    # re-present the same underlying rows, so scanning them multiplies one
    # corrupt row into several findings and returns no primary key to address it
    # by. Repair happens in the base table, so that is what gets swept — the same
    # reason the rule sweep reads the store rather than a render's copy.
    cur.execute("""
        select c.table_name, c.column_name
        from information_schema.columns c
        join information_schema.tables t
          on t.table_schema = c.table_schema and t.table_name = c.table_name
        where c.table_schema = 'public'
          and t.table_type = 'BASE TABLE'
          and c.data_type in ('text', 'character varying')
        order by c.table_name, c.column_name
    """)
    cols = cur.fetchall()

    # Primary key per table, so a hit is addressable rather than merely counted.
    # Read from the pg catalog, not information_schema: the information_schema
    # join on constraint_name alone returned nothing here, and a scan that
    # reports "(no pk)" tells you a row is broken without telling you which row,
    # which is a finding nobody can act on.
    cur.execute("""
        select c.relname, a.attname
        from pg_index i
        join pg_class c on c.oid = i.indrelid
        join pg_namespace n on n.oid = c.relnamespace
        join pg_attribute a on a.attrelid = c.oid and a.attnum = any(i.indkey)
        where i.indisprimary and n.nspname = 'public'
    """)
    pks: dict = {}
    for t, c in cur.fetchall():
        pks.setdefault(t, c)

    like = " or ".join(f"{{col}} like %s" for _ in MARKERS)
    params = [f"%{m}%" for m in MARKERS]

    live_hits, audit_hits, mentions, historical = [], [], [], []
    scanned = 0
    for table, column in cols:
        pk = pks.get(table)
        sel = f'"{pk}"::text' if pk else "'(no pk)'"
        hist = HISTORICAL_SQL.get(table)
        hist_sel = f"({hist})" if hist else "false"
        # No LIMIT. A capped, unordered fetch returned an arbitrary subset, so a
        # column holding more mentions or audit rows than the cap could hide a
        # live-damage row and still print "LIVE RECORDS: OK". Every match is
        # fetched; only the printed lists are abbreviated below.
        q = (f'select {sel}, "{column}", {hist_sel} from "{table}" where '
             + like.format(col=f'"{column}"'))
        try:
            cur.execute(q, params)
        except Exception as e:  # a view or permission we cannot read
            conn.rollback()
            print(f"  skip {table}.{column}: {str(e).splitlines()[0][:80]}")
            continue
        scanned += 1
        for key, val, is_hist in cur.fetchall():
            pos = min((val.find(m) for m in MARKERS if m in val), default=0)
            snip = val[max(0, pos - WINDOW): pos + WINDOW].replace("\n", " ")
            if (table, column) in DOC_COLUMNS or classify(column, val) == "MENTION":
                mentions.append((table, column, key))
                continue
            if table in AUDIT_TABLES:
                audit_hits.append((table, column, key, snip))
            elif is_hist:
                historical.append((table, column, key, snip))
            else:
                live_hits.append((table, column, key, snip))

    print(f"\nscanned {scanned} text column(s) across "
          f"{len({t for t, _ in cols})} base table(s)")
    if mentions:
        print(f"\nMENTIONS — {len(mentions)} row(s) that legitimately WRITE ABOUT "
              "this defect (markers quoted as prose). Not damage:")
        for t, c, k in mentions:
            print(f"  {t}.{c} pk={k}")

    if audit_hits:
        print(f"\nAUDIT TABLES — {len(audit_hits)} row(s). EXPECTED AND CORRECT: "
              "these preserve what actually happened and must not be cleaned.")
        for t, c, k, _ in audit_hits[:10]:
            print(f"  {t}.{c} pk={k}")
        if len(audit_hits) > 10:
            print(f"  ... and {len(audit_hits) - 10} more")

    accepted = [h for h in historical if h[2] in ACCEPTED_HISTORICAL]
    unaccepted = [h for h in historical if h[2] not in ACCEPTED_HISTORICAL]
    if accepted:
        rows = len({(t, k) for t, _, k, _ in accepted})
        print(f"\nHISTORICAL — {rows} closed row(s), ACCEPTED PERMANENTLY AS HISTORY "
              "(Joe's ruling, 2026-08-13). Not actionable; --verbose to list.")
        if "--verbose" in sys.argv:
            for t, c, k, _ in accepted:
                print(f"  {t}.{c} pk={k}")
    # A row corrupted and closed AFTER the ruling has not been accepted by anyone.
    # It is new damage that merely reached a closed state before anyone looked.
    live_hits.extend(unaccepted)
    if unaccepted:
        print(f"\nNOTE: {len(unaccepted)} closed row(s) are NOT in the accepted set — "
              "treated as live damage below.")

    if not live_hits:
        print("\nLIVE RECORDS: OK no tool-call markup in any live text column")
        return 0

    print(f"\nLIVE RECORDS: FAIL {len(live_hits)} row(s) carry tool-call markup")
    for t, c, k, snip in live_hits:
        print(f"  {t}.{c} pk={k}\n      ...{snip}...")
    print("\n  READ the absorbed text before deleting any of it — it IS the "
          "missing field's content, and the parameters after the leaked tag were "
          "written NULL. Repair procedure: rule c53beeaa.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
