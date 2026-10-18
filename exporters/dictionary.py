"""record-layer-dictionary.md — the database's own documentation, rendered for humans.

WHY THIS TARGET EXISTS. The migrations write good prose. 38 views live in `public`,
24 of them carry a COMMENT ON, and 27 table columns carry one as well, several of
them multi-paragraph explanations of a trap or a ruling (`loop_item.domain` runs to
873 characters). All of it was written to be read. None of it can be: it sits in
`pg_catalog`, reachable only by someone already holding a psql prompt, which is
neither of the two people the writing was for. This target moves that prose to the
one place both partners already read, and recompiles it every night so it cannot go
stale the way a hand-copied reference would.

THE PART THAT IS WORTH MORE THAN THE PROSE is the populated fraction beside each
column. The independent audit of 2026-08-02 had to go and MEASURE that `last_touch`
was empty on every lead and every vendor. It was findable only because someone went
looking. Rendered here it becomes a standing number that re-derives itself on every
export, so the next version of that finding shows up on its own instead of waiting
for the next audit.

NO CLIENT DATA. THIS IS A HARD CONSTRAINT AND IT IS ENFORCED IN CODE, NOT INTENDED.
This file renders structure into a document two people read and Drive syncs. Three
rules hold the line:

  1. Every number about live rows comes back from `count()`. `_coverage` builds a
     select of nothing but count expressions and then ASSERTS every value it got
     back is an int. A later edit that slips a plain column into that select raises
     and fails the A8 gate rather than printing somebody's name into the vault.
  2. The only place a row VALUE is read is VOCABULARIES below, a literal tuple of
     seven reference tables. A vocabulary row IS the legal value ('under_loi',
     'roster'), so it carries no person, practice, address, deal or dollar. Because
     the tuple is a literal, no table can join it by being discovered at runtime.
  3. Everything else is catalog: relation names, column names, types, and the prose
     humans typed into COMMENT ON.
  4. THE COMMENT PROSE IS RENDERED VERBATIM, and that is the one place a name can
     reach this file, because a migration author is free to name a real example in
     their own documentation. One does today: 0056's note on `v_ref_index` cites
     "17 live Example Organization 25 rows" to explain the bug it fixed. That is authored
     documentation, not a row read out of the record, and it goes no further than
     the migration already does — this file lands in `DNA/`, which is exactly the
     Joe-and-Dell share, and both of them already see every one of those rows in
     vendors.xlsx. Nothing here is redacted, because redacting an author's own
     sentence would leave a note that no longer says what it meant. Anyone writing
     a COMMENT ON should know it renders here.

  Verified 2026-08-02 against 3,354 live values (name, org, email, phone) pulled
  from v_export_vendors, v_export_leads, v_export_clients, v_export_deals and
  v_ref_index: one match, the authored "Example Organization 25" above. No email address, no
  phone number and no dollar figure appears anywhere in the render.

WHERE IT LANDS, and why. `DNA/Team/` is the shared tier's how-this-works shelf —
dna-protocol.md, twin-system-playbook.md, the record-layer maps, and the adoption
runbook Dell is pointed at on day one. A dictionary of the record is the same kind
of thing those files are, and it is shared-tier by nature: both partners read the
same record and both hit the same "what does status = 'roster' mean" question.
DNA/Reference/ was the other candidate and is wrong — that shelf is market
knowledge (the vertical guides), read while working a deal, not while working the
system.
"""

from datetime import datetime, timezone

import psycopg

DICT_REL = "DNA/Team/record-layer-dictionary.md"

# The closed vocabularies. Rule 2 above: this literal is the ONLY door through
# which a row value reaches the file, and every table behind it is a list of legal
# values. Adding a table here is a decision about client-data exposure, which is
# why it is a hand-written tuple and not a catalog sweep for tables that look
# small or look like reference data.
VOCABULARIES = (
    "client_status",
    "contact_state",
    "deal_phase",
    "lead_stage",
    "loop_domain",
    "party_link_kind",
    "vendor_category",
)


def _q(name):
    """Quote an identifier. Catalog names are trusted, but nothing is interpolated raw."""
    return '"' + name.replace('"', '""') + '"'


def _views(cur):
    """[(name, comment)] for every view in public, commented or not."""
    cur.execute("""
        select c.relname, obj_description(c.oid)
          from pg_class c join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public' and c.relkind = 'v'
         order by c.relname""")
    return cur.fetchall()


def _columns(cur, relname):
    """[(column, type, comment)] straight from the catalog, in declaration order."""
    cur.execute("""
        select a.attname, format_type(a.atttypid, a.atttypmod),
               col_description(a.attrelid, a.attnum)
          from pg_attribute a
          join pg_class c on c.oid = a.attrelid
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public' and c.relname = %s
           and a.attnum > 0 and not a.attisdropped
         order by a.attnum""", (relname,))
    return cur.fetchall()


def _readable(cur):
    """The relations this credential may select from.

    information_schema filters by privilege where pg_class does not, which is the
    whole reason the two disagree: 38 views exist, the export role can read 29. The
    difference is not a defect to hide, it is a fact the file states.
    """
    cur.execute("""
        select table_name from information_schema.tables where table_schema = 'public'""")
    return {r[0] for r in cur.fetchall()}


def _coverage(cur, relname, cols):
    """(total, {column: filled}) or None when this credential cannot read the relation.

    count(nullif(btrim(x::text), '')) rather than count(x): a view that renders an
    empty string and a view that renders NULL look identical to the person reading
    the file, and the person reading the file is who the number is for. The ::text
    cast is what lets one expression serve every type in the schema.

    The savepoint is not optional. A permission error aborts the surrounding
    transaction, and the surrounding transaction is the one that writes the
    export_run row.
    """
    sel = ", ".join(["count(*)"] +
                    [f"count(nullif(btrim({_q(c)}::text), ''))" for c in cols])
    cur.execute("savepoint dict_cov")
    try:
        cur.execute(f"select {sel} from {_q(relname)}")
        got = cur.fetchone()
    except psycopg.Error:
        cur.execute("rollback to savepoint dict_cov")
        return None
    cur.execute("release savepoint dict_cov")
    # RULE 1. Counts are integers. Anything else means the select grew a column that
    # is not an aggregate, and the next thing it would do is print a row value into
    # a file that syncs to Drive. Fail the gate instead.
    if any(not isinstance(v, int) for v in got):
        raise ValueError(f"{relname}: coverage query returned a non-count value; refusing to render")
    return got[0], dict(zip(cols, got[1:]))


def _fill_cell(cov, col):
    """The populated-fraction cell for one column."""
    if cov is None:
        return "not readable"
    total, filled = cov[0], cov[1].get(col, 0)
    if total == 0:
        return "no rows"
    return f"{filled} / {total} ({round(filled / total * 100)}%)"


def _prose(text):
    """A COMMENT ON, folded to one markdown-table-safe line."""
    if not text:
        return ""
    return (text.replace("\\", "\\\\").replace("|", "\\|")
                .replace("\r", " ").replace("\n", " ").strip())


def _table_comments(cur):
    """The 27 column comments recorded on the record's base tables.

    They are not on the views. Every column comment in the schema sits on a table,
    which means a views-only dictionary would carry the 24 view comments and lose
    every one of the longest, most useful notes in the database.
    """
    cur.execute("""
        select c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
               d.description, obj_description(c.oid)
          from pg_description d
          join pg_class c on c.oid = d.objoid
          join pg_namespace n on n.oid = c.relnamespace
          join pg_attribute a on a.attrelid = c.oid and a.attnum = d.objsubid
         where n.nspname = 'public' and d.objsubid > 0 and c.relkind = 'r'
         order by c.relname, a.attnum""")
    return cur.fetchall()


def build_dictionary(tmp_path, cur):
    stamp = datetime.now(timezone.utc)
    readable = _readable(cur)
    views = _views(cur)

    lines = [
        "# The record layer, in plain language",
        "",
        "> **GENERATED from the CARR record layer — do not hand-edit; regenerated nightly.**",
        "> This is the database describing itself: every read surface, the notes the",
        "> migrations wrote about them, how much of each column is actually filled in, and",
        "> the closed lists of legal values. It holds no client, lead, vendor or deal data.",
        "> The only numbers in it are counts.",
        "",
        "## How to read this",
        "",
        "**Filled** is the share of live rows carrying a value in that column. It is counted",
        "fresh on every export. A low number is a gap in what has been captured, and it says",
        "nothing about the people or the deals behind the record. `not readable` means the",
        "export credential has no permission on that surface, so no count was taken there.",
        "Treat it as unknown.",
        "",
        "**Notes** are the comments written into the database by the migration that created",
        "the column. Where a row is blank, nobody has written one yet.",
        "",
    ]

    canonical = []
    empty_cols = []
    documented = sum(1 for _, c in views if c)

    unreadable = len([v for v, _ in views if v not in readable])
    body = ["## The read surfaces", "",
            f"{len(views)} views, {documented} of them carrying a written note. Of those, "
            f"{unreadable} cannot be read by the export credential and are listed with their "
            "structure only.", ""]

    for name, comment in views:
        cols = _columns(cur, name)
        cov = _coverage(cur, name, [c[0] for c in cols]) if name in readable else None
        body += [f"### `{name}`", ""]
        body.append(_prose(comment) if comment else "*No note is recorded on this view.*")
        body += ["", "| Column | Type | Filled | Notes |", "|---|---|---|---|"]
        for col, typ, ccomment in cols:
            fill = _fill_cell(cov, col)
            body.append(f"| `{col}` | {typ} | {fill} | {_prose(ccomment)} |")
            canonical.append([name, col, typ, fill, bool(ccomment)])
            if cov and cov[0] > 0 and cov[1].get(col, 0) == 0:
                empty_cols.append((name, col, cov[0]))
        body.append("")

    # The standing version of the audit finding. Nothing here is ranked or
    # editorialised; it is the subset of the table above where the count came back
    # zero, pulled to the front so it is seen without reading 38 sections.
    summary = ["## Columns nothing is written to", ""]
    if empty_cols:
        summary += [
            "Every one of these is readable and every one came back empty. A blank in any of",
            "them means the value was never captured. Read it as unknown.",
            "",
            "| Surface | Column | Rows |", "|---|---|---|",
        ]
        summary += [f"| `{v}` | `{c}` | {n} |" for v, c, n in empty_cols]
        summary.append("")
    else:
        summary += ["Every readable column carries at least one value.", ""]

    # ---- the closed vocabularies ----
    vocab = [
        "## Closed vocabularies",
        "",
        "These tables define the legal values behind the status, stage, phase and category",
        "columns above. A value that is not in one of these lists cannot be stored.",
        "",
    ]
    denied = []
    for table in VOCABULARIES:
        cols = _columns(cur, table)
        rows = None
        if table in readable:
            cur.execute("savepoint dict_vocab")
            try:
                cur.execute(f"select * from {_q(table)} order by 1")
                rows = cur.fetchall()
                cur.execute("release savepoint dict_vocab")
            except psycopg.Error:
                cur.execute("rollback to savepoint dict_vocab")
                rows = None
        if rows is None:
            denied.append(table)
        cur.execute("select obj_description(('public.' || %s)::regclass)", (table,))
        tcomment = cur.fetchone()[0]

        vocab += [f"### `{table}`", ""]
        vocab.append(_prose(tcomment) if tcomment else "*No note is recorded on this table.*")
        vocab.append("")
        if rows is None:
            vocab += ["Values not shown: the export credential has no `select` on this table.", ""]
            continue
        head = [c[0] for c in cols]
        vocab += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for r in rows:
            vocab.append("| " + " | ".join("" if v is None else _prose(str(v)) for v in r) + " |")
            canonical.append([table, "value", str(r[0]), "vocabulary", True])
        vocab.append("")

    if denied:
        # Stated as a fix, with the exact line, because the alternative is seven
        # sections that say "not shown" and give the reader nowhere to go. The
        # credential is narrow on purpose (migrations/0006_exporter_role.sql); this
        # is the one grant that would fill the section in on the next nightly run.
        # Index 5 is immediately after the section's intro paragraph and its blank
        # line, so the notice reads before the first table rather than butting up
        # against the paragraph above it.
        how_many = ("None of the tables below can" if len(denied) == len(VOCABULARIES)
                    else f"{len(denied)} of the {len(VOCABULARIES)} tables below cannot")
        vocab[5:5] = [
            f"{how_many} be read by the export credential, which is deliberately",
            "narrow, so their values are missing here. One line in a migration fills them in on",
            "the next run:",
            "",
            "```sql",
            "grant select on " + ", ".join(sorted(denied)) + " to carr_exporter;",
            "```",
            "",
        ]

    # ---- the column notes that live on the tables ----
    notes = _table_comments(cur)
    note_block = [
        "## Column notes recorded on the record tables",
        "",
        f"{len(notes)} columns behind the views carry a written note. They are the reasoning and",
        "the traps: why a column exists, what it must not be used for, which ruling set it.",
        "The export credential cannot read most of these tables, so no fill counts are shown.",
        "",
    ]
    current = None
    for relname, col, typ, desc, tcomment in notes:
        if relname != current:
            current = relname
            note_block += ["", f"### `{relname}`", ""]
            if tcomment:
                note_block += [_prose(tcomment), ""]
        note_block += [f"**`{col}`** ({typ})", "", _prose(desc), ""]
        canonical.append([relname, col, typ, "note", True])

    tail = [
        "---",
        "",
        f"*Exported: {stamp.isoformat()} · {len(views)} views · {documented} view notes · "
        f"{len(notes)} column notes · {len(VOCABULARIES)} vocabularies*",
        "",
    ]

    tmp_path.write_text("\n".join(lines + summary + vocab + body + note_block + tail))
    return len(canonical), canonical
