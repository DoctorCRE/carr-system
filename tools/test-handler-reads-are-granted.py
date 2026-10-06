#!/usr/bin/env python3
"""A table a handler reads directly must be granted to somebody.

WHY THIS EXISTS. work-request-card went down in production on 2026-08-27 with
"permission denied for table work_request_triage_receipt". #751 taught the card to
report whether a human or an agent performed each authority act, and its query
reads three receipt tables DIRECTLY. None of the three carried a single grant.

That was harmless for years because every previous reader of those tables went
through a SECURITY DEFINER function, which runs as its owner and never consults
the caller's privileges. The first handler to read such a table itself is the
first to need a grant -- and nothing said so. Postgres checks table privilege when
it PLANS the statement, so the verb failed for every request, receipts or not.

The same shape took standing-context down the same day on public.rule.

WHAT IS CHECKED, and it is deliberately the narrowest form of the question. A
table that is DECLARED in db/schema.sql, NAMED in a FROM or JOIN in a handler, and
carries NO grant to any role at all is broken for every role except its owner.
That is not a judgment about which role should read it; a table with zero grants
cannot be read by the application under any connection. Measured on the tree the
day this was written: 59 tables are referenced by handlers and exactly three had
zero grants -- the three that were down. No false alarms to tune away.

GRANTS COUNT FROM MIGRATIONS TOO, not only from the snapshot. A grant that has
been written but not yet applied and re-snapshotted is still the repository's
answer to "is this granted", and requiring the snapshot first would make the fix
for an outage fail this check until production caught up.

WHAT THIS DOES NOT CATCH, stated so nobody reads more into a pass. It does not
know which role a handler connects as, so a table granted only to carr_writer and
read on the reader connection still passes here. That is a narrower, harder
question about routing; this one only refuses the case that is broken for
everyone.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "db" / "schema.sql"
HANDLERS = sorted((ROOT / "mcp-server" / "src").glob("*.js"))

GRANT = re.compile(r"^\s*grant\s+.+?\s+on\s+table\s+([a-z_]+\.[a-z_]+)\s+to\b", re.I | re.M)
# Captures the privilege list and the role list too, so "granted, but not to the
# role that reads it" can be told apart from "granted to nobody".
GRANT_FULL = re.compile(
    r"^\s*grant\s+([^;]+?)\s+on\s+table\s+([a-z_]+\.[a-z_]+)\s+to\s+([a-z_, ]+);", re.I | re.M | re.S)

# Tables a handler names that the READER is not expected to select, with the
# reason. Empty on purpose: as of 2026-08-27 every declared table any handler
# references is reader-selectable except the four this check was written for.
# An entry here is a claim that a table is only ever read on the writer or
# authority connection — write it with the verb that does it, or fix the grant.
READER_EXEMPT: dict[str, str] = {}
DECLARED = re.compile(r"^CREATE TABLE (?:ONLY )?([a-z_]+\.[a-z_]+)", re.M)
READ = re.compile(r"\b(?:from|join)\s+(ops|public)\.([a-z_]+)\b", re.I)


def main() -> int:
    # A non-table grant must not borrow SELECT from a later statement. Keep
    # multiline and column-scoped table grants while bounding the schema scan.
    assert GRANT_FULL.findall("GRANT EXECUTE ON FUNCTION public.f() TO carr_reader;\n"
                              "GRANT SELECT (id, slug)\nON TABLE public.actor TO carr_reader;") == [
        ("SELECT (id, slug)", "public.actor", "carr_reader")]
    schema = SNAPSHOT.read_text(encoding="utf-8", errors="replace")
    declared = {t.lower() for t in DECLARED.findall(schema)}
    granted = {t.lower() for t in GRANT.findall(schema)}
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        granted |= {t.lower() for t in
                    GRANT.findall(migration.read_text(encoding="utf-8", errors="replace"))}

    # A guard that passes because it parsed nothing is the failure mode here, so
    # the inputs are asserted before a clean verdict is trusted.
    if len(declared) < 100 or len(granted) < 100 or len(HANDLERS) < 5:
        print(f"  FAIL parsed {len(declared)} declared tables, {len(granted)} granted, "
              f"{len(HANDLERS)} handlers — the scan has collapsed and a pass would mean nothing")
        return 1

    referenced: dict[str, set[str]] = {}
    for path in HANDLERS:
        for schema_name, table in READ.findall(path.read_text(encoding="utf-8", errors="replace")):
            referenced.setdefault(f"{schema_name.lower()}.{table.lower()}", set()).add(path.name)

    # SECOND DIRECTION: granted, but not to the reader. work-request-card was taken
    # down by three tables with no grant at all AND by public.tool_call, which is
    # granted to carr_authority and carr_writer and never to carr_reader. Fixing
    # only the first three would have left the card failing on the next statement
    # it planned. A column-scoped grant counts: public.actor gives the reader
    # select (id, slug) and that is what the card uses.
    reader_ok = set()
    for privs, table, roles in GRANT_FULL.findall(schema):
        if "carr_reader" in roles.lower() and ("select" in privs.lower() or "all" in privs.lower()):
            reader_ok.add(table.lower())
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        for privs, table, roles in GRANT_FULL.findall(
                migration.read_text(encoding="utf-8", errors="replace")):
            if "carr_reader" in roles.lower() and ("select" in privs.lower() or "all" in privs.lower()):
                reader_ok.add(table.lower())

    unreadable = sorted(t for t in referenced
                        if t in declared and t in granted
                        and t not in reader_ok and t not in READER_EXEMPT)
    if unreadable:
        print(f"  FAIL {len(unreadable)} table(s) are read by a handler and granted, but not to "
              f"carr_reader. A read verb plans against the reader connection and fails 42501.\n")
        for table in unreadable:
            print(f"  {table}\n      read by: {', '.join(sorted(referenced[table]))}")
            print(f"      Grant select (or the columns it needs) to carr_reader, or record it "
                  f"in READER_EXEMPT with the verb that reads it on another connection.\n")
        return 1

    ungranted = sorted(t for t in referenced if t in declared and t not in granted)
    if ungranted:
        print(f"  FAIL {len(ungranted)} table(s) are read directly by a handler and carry no "
              f"grant to any role. Every call that plans such a statement fails with "
              f"sqlstate 42501, whether or not it would return rows.\n")
        for table in ungranted:
            print(f"  {table}\n      read by: {', '.join(sorted(referenced[table]))}")
            print(f"      Grant select to the role that connection uses, in a migration.\n")
        return 1

    print(f"  ok   {len(referenced)} handler-read relations, all granted and reader-selectable "
          f"({len(declared)} declared tables, {len(granted)} with grants, "
          f"{len(reader_ok)} reader-selectable)")
    print("handler-read grant selftest: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
