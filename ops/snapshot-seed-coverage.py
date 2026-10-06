#!/usr/bin/env python3
# ci: selftest — the paired suite is ops/snapshot-seed-coverage-selftest.py; this
# module is a library for bin/schema-snapshot.sh, not a standalone gate.
"""Refuse a schema snapshot that cannot rebuild the database it was taken from.

THE TRAP, sprung six times before this file existed. A migration seeds rows. The
snapshot's ledger absorbs that migration, so on a rebuild it is already "applied"
and never replays. If the snapshot did not also carry the rows, the rebuilt
database has the table and none of its contents, and the failure surfaces days
later as a db-gate red in CI with nothing pointing at the cause. Each of the six
was then patched one table at a time, by hand, in bin/schema-snapshot.sh.

WHAT THIS CHECKS, and it is deliberately narrow: every table that an ALREADY
APPLIED migration writes rows into AT MIGRATION TIME must be CLASSIFIED in
ops/config/snapshot-seed-coverage.json -- carried, carried_after_apply,
carried_subset, or explicitly excluded with a reason. carried_after_apply is
the two-phase source state: absence is allowed only while a repository migration
that seeds the table remains outside the artifact ledger; once it enters the
ledger, the same row-presence requirement as carried applies. A table that is
none of those is the next instance, and the snapshot refuses to be written until
someone classifies it.

WHAT "AT MIGRATION TIME" MEANS, and the first version of this file got it wrong.
It is NOT "appears in an INSERT statement in the file". An independent review of
the first version (2026-08-27) failed it on two blind spots that this repository's
own migrations already exercise:

  * DEFINE-THEN-CALL. 0247_system_rule_scope_binding.sql creates
    ops.sync_system_rule_control_bindings(), whose body inserts into
    ops.rule_control_binding, and then CALLS it at line 159. The first version
    stripped every routine body as "runtime code" and saw nothing in that file.
    A function body is runtime code only until the migration invokes it. So
    routine bodies are stripped, then put BACK for any routine the migration
    actually calls -- transitively, because a called routine may call another.

  * DML THAT IS NOT "INSERT INTO". CREATE TABLE ... AS SELECT, MERGE, and
    COPY ... FROM all land rows and were all invisible. SELECT ... INTO is
    recognised only at top level, never inside a DO block or routine body, where
    PL/pgSQL's SELECT ... INTO assigns a variable and names no table at all.

WHAT IT DOES NOT DO, on purpose. It never adds a table to the snapshot and never
decides that a table deserves carrying. bin/schema-snapshot.sh's own rule still
governs that and is quoted here because this check is the thing most likely to
erode it: "a table qualifies because someone read its rows and can say what is in
them, never because a migration seeded it." Detection is not permission to
auto-dump. All this does is refuse to let an unclassified table pass in silence.

BOTH DIRECTIONS ARE CHECKED. A declared-carried table that has fallen out of the
artifact is the trap again, arriving by deletion. A declared-excluded table that
has APPEARED in the artifact is worse: business or runtime data entering a
tracked file, which is the one outcome the snapshot's whole design prevents.

READS THE ARTIFACT, NOT THE DATABASE. The applied-migration ledger and the data
statements both live in the snapshot being written, so this runs offline against
the candidate file: deterministic, testable without production, and true of the
exact bytes about to be committed rather than of a second query whose answer
could differ.
"""

import json
import os
import re
import sys

DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|)\$")

# THE OTHER HOT SPOT, alongside EXECUTE_ARGUMENT: scan_sql's main loop advanced
# one character at a time even through long runs of perfectly ordinary text
# (identifiers, keywords, whitespace -- the bulk of any SQL file), each such
# character taking its own trip through append_top and the pending-chunk
# bookkeeping. Profiling PR #1297's db/schema.sql (24MB, no unusually long
# comments or literals) showed exactly that: tens of millions of one-character
# append_top calls.
#
# ORDINARY_RUN finds the next position that could possibly start a construct
# the scan needs to treat specially: a string literal ("'"), a dollar-quote or
# dollar-quoted string ("$"), a line comment ("--") or a block comment ("/*").
# Whenever the current position is none of those (the `else` branch below),
# every character up to that next position is going to be appended verbatim
# exactly as scan_sql already does one at a time -- so it is appended in one
# slice instead. This changes nothing about which characters take which
# branch, only how many Python-level steps it costs to get there: a run of
# 10,000 ordinary characters becomes one slice-and-append instead of 10,000.
#
# A lone "-" or "/" that fails its own branch's more specific test (not part of
# "--" or "/*") already falls into this same `else` branch today and is
# appended as ordinary text, so folding it into a bulk slice changes nothing
# about its handling either.
ORDINARY_RUN = re.compile(r"'|\$|--|/\*")
TABLE = r"(\"?[a-z_][a-z0-9_]*\"?(?:\s*\.\s*\"?[a-z_][a-z0-9_]*\"?)?)"
LEDGER_COPY = re.compile(r"^COPY\s+public\.schema_migrations\s*\(", re.M)
COPY_BLOCK = re.compile(r"^COPY\s+" + TABLE + r"\s*(?:\([^)]*\)\s*)?FROM\s+stdin\s*;", re.I | re.M)

# EXECUTE MAKES A STRING LITERAL INTO STATEMENTS. Blanking literals is right for
# every other case and wrong for exactly this one: `execute 'insert into t ...'`
# lands rows, and so does `execute format('insert into t ...', ...)`. The scanner
# blanked both and detected nothing, while scan_sql's own docstring justified the
# blanking with "no row-landing statement hides inside a string" -- which is
# precisely false here. Ordinary and dollar-quoted EXECUTE literals both run SQL.
#
# format() is matched as an optional wrapper rather than a separate case because
# the literal is still the FIRST argument; a table interpolated as %s cannot be
# recovered by anyone and is not pretended to be, but a literal table name in the
# format string is read exactly like any other. Variables, concatenated fragments,
# and escape-prefixed EXECUTE literals are not decoded as dynamic SQL.
EXECUTE_ARGUMENT = re.compile(
    r"(?:^|[^A-Za-z0-9_])execute\s*(?:format\s*\(\s*)?$", re.I)

CREATE_ROUTINE = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:function|procedure)\s+"
    r"([a-z_][a-z0-9_]*(?:\s*\.\s*[a-z_][a-z0-9_]*)?)\s*\(", re.I)

# Statements that NAME a function without invoking it, masked to the END OF THE
# STATEMENT rather than to the end of one signature. That distinction is the whole
# fix: a privilege statement may list many functions --
#
#     grant execute on function
#       ops.select_provider_routes(text[]),
#       ops.record_provider_observation(text,text,integer,text,integer,text),
#       ops.put_cognition_cache(text,text,integer,integer,jsonb,text[],integer)
#     to carr_jobs;
#
# -- and masking only the first left the rest looking like calls, so their bodies
# were pulled in as migration-time writes. An independent review measured the
# damage: 49 of 153 detections were routine bodies admitted this way, none of
# which the migration ever runs. Masking to the semicolon covers a list of any
# length.
MENTIONS_ROUTINE = re.compile(
    r"(?:create\s+(?:or\s+replace\s+)?(?:function|procedure)|revoke\b[^;]*?\bon\s+(?:function|procedure)"
    r"|grant\b[^;]*?\bon\s+(?:function|procedure)|alter\s+(?:function|procedure)"
    r"|drop\s+(?:function|procedure)"
    r"|comment\s+on\s+(?:function|procedure)"
    r"|security\s+label\b[^;]*?\bon\s+(?:function|procedure))[^;]*;", re.I | re.S)


# Row-landing DML. Every form here has been seen in this repository's migrations.
WRITES_ANYWHERE = (
    re.compile(r"insert\s+into\s+(?:only\s+)?" + TABLE, re.I),
    re.compile(r"merge\s+into\s+" + TABLE, re.I),
    re.compile(r"copy\s+" + TABLE + r"\s*(?:\([^)]*\)\s*)?from\b", re.I),
    # TEMP and TEMPORARY are deliberately NOT matched. Thirteen migrations build
    # scratch tables this way for before/after comparison (_org_before, amd2_map,
    # lt_before, ...); they vanish with the session and can never be in a snapshot,
    # so counting them is a pure false alarm — and a check that cries wolf gets
    # switched off, which is the failure mode this whole file exists to avoid.
    re.compile(r"create\s+(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?"
               + TABLE + r"[^;]*?\bas\s+(?:with|select)", re.I | re.S),
)

# ROW EVIDENCE IS A NARROWER QUESTION THAN PRESENCE, AND THE TWO DIRECTIONS WANT
# DIFFERENT ANSWERS. Reading both from one set left the carried direction a NAME
# test for one statement shape.
#
# `copy public.deal_phase from '/tmp/x.csv';` is a real write when a migration
# runs it, and an artifact carrying that line is still something to refuse if the
# table is EXCLUDED -- business or runtime data entering a tracked file is the one
# outcome this whole design prevents, so presence stays deliberately broad there.
# But it is not evidence the artifact CARRIES rows: the data sits in a file the
# snapshot does not contain and a rebuild would never see. A carried table must
# not pass on the strength of its name.
#
# So the carried direction reads this narrower set. COPY FROM stdin is absent
# from it on purpose: copy_blocks() already counts those blocks, and it counts
# ROWS, which is the whole reason that direction is a row test.
ARTIFACT_ROW_WRITES = tuple(
    pattern for pattern in WRITES_ANYWHERE
    if "copy" not in pattern.pattern[:8].lower())

# SELECT ... INTO IS DELIBERATELY NOT DETECTED, and the reason is worth stating so
# nobody adds it back. In plain SQL it creates a table; in PL/pgSQL the identical
# syntax assigns a variable. Every occurrence across all 264 migrations is the
# variable form — `into n`, `into missing`, `into dup`, `into changed` — and one
# more sits inside a comment in 0078 ("select side into s from participant_role"),
# which a regex cannot tell from code without a real SQL parser. Detecting it
# produced only false alarms. No migration here seeds a persistent table this way;
# CREATE TABLE ... AS covers the case that matters.


def normalise(table):
    """public.foo and foo are the same table; ops.foo is not."""
    table = table.replace('"', "").replace(" ", "").lower()
    return table[len("public."):] if table.startswith("public.") else table


# Prefix checks read an incrementally maintained suffix with every whitespace
# run collapsed to one space. The supported prefixes are short token sequences;
# arbitrary padding between any of their tokens cannot consume this window.
HEAD_TAIL = 4096

# The routine-header scan is bounded far more generously because it looks for the
# LAST `create function` header before this body, which sits behind the whole
# parameter list rather than adjacent to the quote.
#
# ITS DEGRADATION DIRECTION IS THE SAFE ONE, which is why a bound is acceptable
# here at all. If the header falls outside the window, `headers` comes back empty
# and the body is appended to the top-level text instead of being filed under a
# routine name. Top-level text is scanned unconditionally, so the body's tables
# are still detected -- they simply stop being gated on the routine being CALLED.
# That is a false alarm at worst, never a missed seed, and this file's whole
# purpose is to refuse rather than to miss.
ROUTINE_TAIL = 65536


def _tail(parts, count):
    """The last `count` characters of an accumulated buffer, without joining it.

    THE CALLER ASKS FOR MORE CHARACTERS THAN THE TEST INSPECTS, on purpose. The
    E-string pattern reads two characters, so a 2-character tail looks sufficient
    and is not: truncation turns whatever preceded them into START OF STRING, and
    the pattern's own `^` alternative then matches where the full buffer refused.
    "E_b_cE\n" is one -- the real buffer has an alphanumeric before the final E so
    the test is false, while its last two characters "E\n" make it true. Python's
    `$` also matches before a trailing newline, which puts the interesting
    characters up to three back. A 300,000-case fuzz over both forms diverges 650
    times at two characters and zero times at three; four is that with margin.

    WHY THIS EXISTS AND IS NOT A MICRO-OPTIMISATION. The E-string test below looks
    at two characters, and used to reach them with "".join(top) -- rebuilding the
    entire accumulated buffer once per string literal. db/schema.sql holds 23,696
    of them, so the pass was quadratic in artifact size: 1.5s at 0.5MB, 7.4s at
    1MB, 28.9s at 1.5MB, and the artifact is 2.3MB and grows with every migration.
    That is not merely slow. ops/ci.sh gives each gate selftest
    CI_SELFTEST_TIMEOUT_SECONDS, and on exit 124 it sets gates_timed_out and
    BREAKS -- abandoning every remaining gate selftest in the class, not just this
    one. A check that eventually times out takes the rest of the class with it.
    """
    out, size = [], 0
    for chunk in reversed(parts):
        out.append(chunk)
        size += len(chunk)
        if size >= count:
            break
    return "".join(reversed(out))[-count:]


def _scanned(body):
    """A nested body, run through the same scan and flattened.

    A body's own routines and DO blocks are folded in with it: anything a called
    routine's body reaches is reached when that routine runs.
    """
    inner_top, inner_dos, inner_routines = scan_sql(body)
    parts = [inner_top, *inner_dos]
    for bodies in inner_routines.values():
        parts.extend(bodies)
    return " ".join(parts)


def scan_sql(sql):
    """One left-to-right pass over a migration: strip comments, blank string
    literals, and split out dollar-quoted bodies.

    WHY A SCAN AND NOT MORE REGULAR EXPRESSIONS. The previous version masked
    string literals with a pattern applied to text that still contained SQL
    comments, so an apostrophe in ordinary English prose paired with the opening
    quote of a real string and masked everything between them -- including the
    CALL that makes a routine body count as a seed. One apostrophe in one comment
    disarmed the check for that whole migration, and 102 of 264 applied
    migrations already have odd apostrophe parity. Found by the fourth
    independent review.

    You cannot find comments without knowing where strings are, and you cannot
    find strings without knowing where comments are. Alternating regular
    expressions will always lose that race; a single pass that handles both, plus
    dollar quoting, cannot.

    Literals are blanked rather than kept, because a table name is an identifier
    and an `insert into` quoted in prose is not a statement. THE ONE EXCEPTION IS
    EXECUTE, and an earlier version of this docstring stated the rule without it:
    "no row-landing statement hides inside a string" is false for
    `execute 'insert into t ...'`, which is a string and does land rows. A
    literal that is an argument to EXECUTE is scanned as SQL; every other literal
    is still blanked.

    Bodies are scanned too, not stored raw. A DO block or a routine body has its
    own comments and its own string literals, and leaving them unscanned put the
    apostrophe bug straight back one level down -- caught by the suite case that
    pins a signature inside has_function_privilege() as a name rather than a call.

    Returns (top_level_text, do_bodies, routines) with routines keyed by name.
    """
    top, do_bodies, routines = [], [], {}
    # Keep the output as bounded chunks while scanning. The backward-looking
    # checks need only the tail, but appending one character per iteration made
    # every tail request walk thousands of list entries. Flushing before a tail
    # request preserves the exact accumulated text while making that walk span
    # chunks instead of characters.
    pending_top, pending_size = [], 0

    # Prefix context is separate from output: comments and inert literals add
    # whitespace to output, but never make the next query revisit old chunks.
    # Update once per appended run, including whitespace across chunk joins.
    prefix_tail = ""
    execute_distance = HEAD_TAIL + 1
    execute_carry = ""

    def append_top(value):
        nonlocal pending_size, prefix_tail, execute_distance, execute_carry
        if value:
            compact = re.sub(r"\s+", " ", value)
            if prefix_tail.endswith(" ") and compact.startswith(" "):
                compact = compact[1:]
            if compact:
                # Measure the EXECUTE prefilter in the same normalized text as
                # the prefix checks; raw padding must not disqualify a literal.
                combined = execute_carry + compact
                found = combined.lower().rfind("execute")
                execute_distance = (execute_distance + len(compact) if found == -1
                                    else len(combined) - found - len("execute"))
                execute_carry = combined[-6:]
                prefix_tail = (prefix_tail + compact)[-HEAD_TAIL:]
            pending_top.append(value)
            pending_size += len(value)
            if pending_size >= 4096:
                top.append("".join(pending_top))
                pending_top.clear()
                pending_size = 0

    def execute_argument():
        return execute_distance <= HEAD_TAIL and bool(EXECUTE_ARGUMENT.search(prefix_tail))

    def flush_top():
        nonlocal pending_size
        if pending_top:
            top.append("".join(pending_top))
            pending_top.clear()
            pending_size = 0

    def tail(count):
        flush_top()
        return _tail(top, count)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):                 # line comment
            end = sql.find("\n", i)
            i = n if end == -1 else end
            append_top(" ")
        elif ch == "/" and sql.startswith("/*", i):               # block comment, nestable
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            append_top(" ")
        elif ch == "'":                                           # string literal
            # E'...' takes BACKSLASH escapes; a plain literal does not (server
            # default standard_conforming_strings). Knowing only the '' form let
            # E'the reviewer\\'s registry' end the literal at the escaped quote, so
            # the rest of the migration was read inside-out and a plainly top-level
            # INSERT went unreported. That is the apostrophe class of R4 one escape
            # form over, and it swallowed real DML rather than only a call.
            escaped = bool(re.search(r"(?:^|[^A-Za-z0-9_])[Ee]$", tail(4)))
            # IS THIS LITERAL AN ARGUMENT TO EXECUTE? If so its text is not inert
            # -- it is SQL that runs. Decided BEFORE the literal is consumed,
            # because `top` still ends at the character before the quote here.
            dynamic = execute_argument()
            opened = i
            i += 1
            while i < n:
                if escaped and sql[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if sql[i] == "'":
                    if sql.startswith("''", i):
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            if dynamic:
                # The literal's own doubled quotes are how a quote is spelled
                # inside it; undo that before reading the text as SQL.
                inner = sql[opened + 1:i - 1].replace("''", "'")
                append_top(" " + _scanned(inner) + " ")
            else:
                append_top(" ")
        elif ch == "$":
            match = DOLLAR.match(sql, i)
            if not match:
                append_top(ch)
                i += 1
                continue
            tag = match.group(0)
            end = sql.find(tag, match.end())
            if end == -1:                                         # unterminated: keep verbatim
                append_top(sql[i:])
                break
            body = sql[match.end():end]
            head = prefix_tail
            previous = re.search(r"([A-Za-z_]+)\s*$", head)
            keyword = previous.group(1).lower() if previous else ""
            # `do language plpgsql $$ ... $$` is the same statement as `do $$ ... $$`.
            # Matching only the bare word left the body blanked as a string literal
            # and the block invisible.
            if keyword != "do" and re.search(r"(?:^|;|\s)do\s+language\s+[a-z_]+\s*$", head, re.I):
                keyword = "do"
            # `execute $$ ... $$` is the same statement as `execute '...'`, with the
            # other spelling of a string. Handled here rather than by widening the
            # literal branch, because a dollar-quote is consumed by this branch.
            if keyword == "execute" or execute_argument():
                append_top(" " + _scanned(body) + " ")
                i = end + len(tag)
                continue
            if keyword == "as":
                headers = list(CREATE_ROUTINE.finditer(tail(ROUTINE_TAIL)))
                if headers:
                    routines.setdefault(normalise(headers[-1].group(1)), []).append(_scanned(body))
                else:
                    append_top(" " + _scanned(body) + " ")
            elif keyword == "do":
                do_bodies.append(_scanned(body))
            else:
                append_top(" ")                                   # dollar-quoted string literal
            i = end + len(tag)
        else:
            match = ORDINARY_RUN.search(sql, i)
            end = match.start() if match else n
            append_top(sql[i:end])
            i = end
    flush_top()
    return "".join(top), do_bodies, routines


def migration_time_text(sql):
    """Every stretch of SQL that actually executes when this migration runs.

    Top level and DO blocks always execute. A routine body executes only if the
    migration calls it, so routines are pulled in by transitive closure over the
    text that already executes, with definition sites masked out first.
    """
    top, do_bodies, routines = scan_sql(sql)
    executing = [top] + do_bodies
    called, changed = set(), True
    while changed:
        changed = False
        blob = MENTIONS_ROUTINE.sub(" ", " ".join(executing))
        for name, bodies in routines.items():
            if name in called:
                continue
            short = re.escape(name.split(".")[-1])
            if re.search(r"\b" + short + r"\s*\(", blob, re.I):  # PERFORM/SELECT/CALL all match
                called.add(name)
                executing.extend(bodies)
                changed = True
    return top, executing


def written_tables(sql):
    """Tables this migration lands rows in at migration time."""
    _top, executing = migration_time_text(sql)
    found = set()
    for segment in executing:
        for pattern in WRITES_ANYWHERE:
            found.update(normalise(hit.group(1)) for hit in pattern.finditer(segment))
    return found


def applied_migrations(artifact):
    """The filenames in the artifact's own applied-migration ledger."""
    match = LEDGER_COPY.search(artifact)
    if not match:
        raise LookupError(
            "the artifact carries no public.schema_migrations ledger; "
            "the snapshot is malformed and nothing can be judged against it")
    applied = set()
    for line in artifact[match.end():].splitlines()[1:]:
        if line.startswith("\\."):
            break
        first = line.split("\t", 1)[0].strip()
        if first:
            applied.add(first)
    return applied


def seeded_tables(repo, applied):
    """table -> sorted migrations that write rows into it at migration time.

    Also returns ledger entries with no file on disk. Those used to be skipped in
    silence, which meant a renamed migration quietly removed its tables from the
    question -- so they are reported rather than dropped.
    """
    seeds, missing = {}, []
    directory = os.path.join(repo, "migrations")
    present = set(os.listdir(directory))
    for name in sorted(applied):
        if name not in present:
            missing.append(name)
            continue
        with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as handle:
            for table in written_tables(handle.read()):
                seeds.setdefault(table, set()).add(name)
    return {t: sorted(m) for t, m in seeds.items()}, sorted(missing)


def data_region(artifact):
    """Everything from the ledger COPY to EOF.

    pg_dump emits every CREATE FUNCTION, every COMMENT and all other DDL before
    the first COPY, so this boundary separates data statements from prose that
    merely quotes one. Without it a COMMENT reading "replaces the blind `insert
    into party ...`" makes the business table `party` look carried.
    """
    match = LEDGER_COPY.search(artifact)
    return artifact[match.start():] if match else ""


def check_region_boundary(artifact):
    """The ledger COPY must be the FIRST COPY, or the boundary above is a lie.

    This held only because bin/schema-snapshot.sh dumps the ledger before the
    vocabulary. Reordering those two would silently blind the excluded-but-present
    direction, so the positional assumption is asserted rather than assumed.
    """
    ledger = LEDGER_COPY.search(artifact)
    if not ledger:
        return None
    # Not COPY alone: the two data blocks bin/schema-snapshot.sh appends by hand
    # (the SIEP manifest, doctrine_meta) are INSERTs, so an INSERT above the ledger
    # would sit outside the data region and go unread just as a COPY would.
    # Only statements OUTSIDE routine bodies count. The DDL above the ledger is
    # full of CREATE FUNCTION bodies whose own INSERTs start a line, and reading
    # those as data made this check refuse every real snapshot.
    prefix = artifact[:ledger.start()]
    # DO BODIES ARE INCLUDED AND ROUTINE BODIES ARE NOT, and the asymmetry is the
    # whole point. A DO block above the ledger RUNS on a rebuild, so rows it lands
    # are data sitting outside the region tables_with_data can see -- invisible to
    # both checks at once. This is not hypothetical: bin/schema-snapshot.sh emits
    # the CARR ROLE PREAMBLE as a DO block above the ledger, so the construct is
    # already there and only its contents have been harmless. A routine body above
    # the ledger is a DEFINITION; the DDL half of the artifact is full of them and
    # reading their INSERTs as data made an earlier version refuse every real
    # snapshot.
    prefix_top, prefix_dos, _routines = scan_sql(prefix)
    # NOT anchored to the start of a line. Data above the ledger is data wherever
    # it sits on the line, and the anchor bought nothing: with it and without it the
    # real artifact is equally clean, so all it did was give a mutation somewhere to
    # hide. Literals and comments are already gone from prefix_top, so a table merely
    # NAMED in prose cannot reach here.
    # ONE ORDERED LIST, NOT A MINIMUM OVER SEVERAL. An earlier version took
    # min(...) by match.start() across every segment, and those offsets are
    # positions in DIFFERENT strings: a DO body's offset 10 is not earlier than
    # top-level text's offset 400, and comparing them named whichever table the
    # arithmetic happened to favour. Segments are searched in order instead --
    # top-level text, then each DO body -- which is a real order and a stable one.
    #
    # re.M is NOT passed. No WRITES_ANYWHERE pattern is anchored, so it changed
    # nothing and only gave a mutation a place to hide.
    first = None
    for segment in [prefix_top, *prefix_dos]:
        hits = [m for m in (pattern.search(segment) for pattern in WRITES_ANYWHERE) if m]
        if hits:
            first = min(hits, key=lambda m: m.start())
            break
    if first:
        return (f"DATA REGION BOUNDARY MOVED: a data statement in the artifact is "
                f"{normalise(first.group(1))}, above public.schema_migrations.\n"
                f"    This check reads data statements from the ledger COPY to EOF. With data\n"
                f"    above it, rows in that block are invisible here and an excluded table\n"
                f"    could enter a tracked file unseen. Restore the ledger-first order in\n"
                f"    bin/schema-snapshot.sh, or teach this check the new boundary.")
    return None


def copy_blocks(region):
    """Every COPY ... FROM stdin block: its table, whether it holds rows, its span.

    pg_dump --data-only emits the COPY HEADER for a table that holds no rows at
    all, so matching the header proves the table was considered and never that
    anything was carried. Counting the block is what makes the carried direction a
    ROW test instead of a NAME test. Without it a carried table emptied in
    production passes clean, which is this file's own trap arriving by deletion
    rather than by omission -- and three of the tables it would hide (deal_phase,
    ops.guidance_registry, retrieval_ranking_policy) are ones whose emptiness has
    already turned a db-gate red.

    The span is returned so the caller can take the block OUT before scanning. COPY
    data is not SQL: an apostrophe in a data row would send a literal scanner
    inside-out over everything after it, which is finding 4 one region over.

    ONLY `FROM stdin` BLOCKS ARE COUNTED, and the asymmetry is deliberate rather
    than overlooked. A `COPY t FROM '/path'` has no inline block to count, so its
    rows can only be confirmed by reading a file this check cannot see; presence of
    the statement is all there is, and it stays a name test. bin/schema-snapshot.sh
    emits no such form -- pg_dump uses stdin -- so nothing in this artifact takes
    that path today, and a suite case pins that it would still register rather than
    vanish if one ever appeared.
    """
    blocks, unterminated = [], []
    cursor = 0
    terminator = re.compile(r"^\\\.\r?$", re.M)
    while (head := COPY_BLOCK.search(region, cursor)) is not None:
        end = terminator.search(region, head.end())
        if end is None:
            # Preserve the SQL text for conservative presence scanning, and
            # refuse the malformed outer block. Its data cannot open new blocks.
            unterminated.append(normalise(head.group(1)))
            break
        body = region[head.end():end.start()]
        blocks.append((normalise(head.group(1)),
                       any(line.strip() for line in body.split("\n")),
                       head.start(), end.end()))
        cursor = end.end()
    return blocks, unterminated


def _blank(text, spans):
    """Replace spans with whitespace, keeping newlines so line structure survives."""
    parts, cursor = [], 0
    for start, stop in sorted(spans):
        if start < cursor:
            continue
        parts.append(text[cursor:start])
        parts.append(re.sub(r"[^\n]", " ", text[start:stop]))
        cursor = stop
    parts.append(text[cursor:])
    return "".join(parts)


def tables_with_data(artifact):
    """Returns (present, carrying_rows) -- two answers, because the two directions
    of the check are asking different questions.

    PRESENT is deliberately broad: anything that would land rows counts, including
    a file-sourced COPY whose data the artifact does not contain. That set drives
    the excluded direction, where the failure being caught is business or runtime
    data entering a tracked file and a name is reason enough to stop.

    CARRYING_ROWS is the row test: COPY blocks are counted only when they hold
    rows, and a file-sourced COPY does not count at all. That set drives the
    carried direction, where the failure being caught is a snapshot that cannot
    rebuild its own database, and a table's NAME appearing proves nothing.

    Two passes, and each one can change an answer. COPY blocks are counted, because
    a header above an empty block carries nothing. Everything else is read from
    SCANNED text, because the blocks bin/schema-snapshot.sh appends pass whole rows
    as jsonb string literals: an `insert into party` occurring inside one of those
    literals is row DATA, not a statement, and reading the region raw let it
    register as a table being present.
    """
    region = data_region(artifact)
    found, spans = set(), []
    blocks, _unterminated = copy_blocks(region)
    for name, rows, start, stop in blocks:
        if rows:
            found.add(name)
        spans.append((start, stop))
    rows_only = set(found)
    top, do_bodies, routines = scan_sql(_blank(region, spans))
    segments = [top, *do_bodies]
    for bodies in routines.values():
        segments.extend(bodies)
    for segment in segments:
        for pattern in WRITES_ANYWHERE:
            found.update(normalise(hit.group(1)) for hit in pattern.finditer(segment))
        for pattern in ARTIFACT_ROW_WRITES:
            rows_only.update(normalise(hit.group(1)) for hit in pattern.finditer(segment))
    return found, rows_only


def load_classification(repo):
    path = os.path.join(repo, "ops", "config", "snapshot-seed-coverage.json")
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    carried = dict(doc.get("carried") or {})
    after_apply = dict(doc.get("carried_after_apply") or {})
    subset = dict(doc.get("carried_subset") or {})
    excluded = dict(doc.get("excluded") or {})
    forbidden = dict(doc.get("carried_subset_must_not_contain") or {})
    buckets = (
        ("carried", carried),
        ("carried_after_apply", after_apply),
        ("carried_subset", subset),
        ("excluded", excluded),
    )
    for index, (name_a, bucket_a) in enumerate(buckets):
        for name_b, bucket_b in buckets[index + 1:]:
            both = sorted(set(bucket_a) & set(bucket_b))
            if both:
                raise ValueError(f"a table cannot be both {name_a} and {name_b}: " + ", ".join(both))
    stray = sorted(set(forbidden) - set(subset))
    if stray:
        raise ValueError("carried_subset_must_not_contain names a table that is not "
                         "carried_subset: " + ", ".join(stray))
    return carried, after_apply, subset, excluded, forbidden, path


def check(repo, artifact_text):
    """Return a list of human-readable failures; empty means the snapshot is sound."""
    carried, after_apply, subset, excluded, forbidden, config_path = load_classification(repo)
    rel_config = os.path.relpath(config_path, repo)
    applied = applied_migrations(artifact_text)
    seeds, missing = seeded_tables(repo, applied)
    migration_dir = os.path.join(repo, "migrations")
    pending = {
        name for name in os.listdir(migration_dir)
        if name.endswith(".sql") and name not in applied
    }
    pending_seeds, _pending_missing = seeded_tables(repo, pending)
    present, carrying_rows = tables_with_data(artifact_text)
    failures = []

    boundary = check_region_boundary(artifact_text)
    if boundary:
        failures.append(boundary)

    _blocks, unterminated = copy_blocks(data_region(artifact_text))
    for table in unterminated:
        failures.append(
            f"COPY BLOCK NEVER ENDS: {table}\n"
            f"    Its COPY ... FROM stdin has no terminating line, so the artifact is\n"
            f"    truncated or malformed. Nothing after it can be read as a statement,\n"
            f"    which would hide an excluded table's rows rather than report them.\n"
            f"    Regenerate the snapshot; do not hand-edit around this.")

    for name in missing:
        failures.append(
            f"LEDGER NAMES A MIGRATION THAT IS NOT ON DISK: {name}\n"
            f"    It is applied, so a rebuild will not replay it, and its file is gone — so\n"
            f"    whatever it seeded cannot be checked. Restore the file or record the rename.")

    for table in sorted(
        t for t in seeds
        if t not in carried and t not in after_apply and t not in subset and t not in excluded
    ):
        failures.append(
            f"UNCLASSIFIED SEEDED TABLE: {table}\n"
            f"    written at migration time by: {', '.join(seeds[table])}\n"
            f"    (already in this snapshot's ledger, so that migration will NOT replay on a\n"
            f"    database rebuilt from this file)\n"
            f"    Decide and record it in {rel_config}: 'carried' if a rebuild needs its rows\n"
            f"    (then add the block to bin/schema-snapshot.sh), or 'excluded' with the reason\n"
            f"    it is business, runtime or usage data. Read the rows before carrying them.")

    classified = set(carried) | set(subset) | set(excluded)
    for table in sorted(classified - set(seeds)):
        # An exclusion is safe to decide before its source-only migration is
        # absorbed: the pending migration still replays, and the artifact must
        # remain empty for that runtime/business table. Keep refusing truly
        # stale exclusions that have neither an applied nor pending seed.
        if table in excluded and table in pending_seeds:
            continue
        failures.append(
            f"CLASSIFICATION ENTRY NO LONGER APPLIES: {table}\n"
            f"    {rel_config} classifies this table, and no applied migration writes rows\n"
            f"    into it at migration time any more. That is how a false alarm becomes\n"
            f"    permanent: an entry nobody revisits, for a question nobody is asking.\n"
            f"    Remove it, or say why detection changed.")

    for table in sorted(set(carried) | set(subset)):
        if table not in carrying_rows:
            failures.append(
                f"DECLARED CARRIED BUT ABSENT: {table}\n"
                f"    {rel_config} says this snapshot carries it, and it is not in the artifact.\n"
                f"    Reason on file: {carried.get(table, subset.get(table, '(none recorded)'))}\n"
                f"    Its block in bin/schema-snapshot.sh is not emitting rows for it, so a\n"
                f"    rebuild from this file would come up short. This check reads the ARTIFACT\n"
                f"    and never the database, so it cannot tell you whether production still\n"
                f"    holds the rows — do not read it as saying they are gone.")

    for table in sorted(after_apply):
        if table in seeds:
            if table not in carrying_rows:
                failures.append(
                    f"""DECLARED CARRIED AFTER APPLY BUT ABSENT: {table}
    An applied migration now seeds this table, so its two-phase allowance
    is over and the artifact must carry rows for it.
    Reason on file: {after_apply[table]}
    Fix the bounded emit block in bin/schema-snapshot.sh; do not weaken
    the classification or hand-edit the artifact.""")
        elif table in pending_seeds:
            if table in present:
                failures.append(
                    f"""CARRIED-AFTER-APPLY TABLE PRESENT BEFORE ITS LEDGER ENTRY: {table}
    The artifact carries data for a table whose seeding migration is still
    pending. Remove the premature data; source order must stay ledger-first.""")
        else:
            failures.append(
                f"""CARRIED-AFTER-APPLY ENTRY HAS NO SOURCE SEED: {table}
    No applied or pending migration seeds this table at migration time.
    Remove the stale classification or restore the migration that justifies it.""")

    for table in sorted(excluded):
        if table in present:
            failures.append(
                f"DECLARED EXCLUDED BUT PRESENT: {table}\n"
                f"    {rel_config} excludes this table, and the artifact carries rows for it.\n"
                f"    Reason on file: {excluded[table]}\n"
                f"    A tracked snapshot must never widen into business, runtime or usage data.")

    # A partially-carried table is carried by a scoped render, and the scope lives
    # in a WHERE clause this file cannot read. What it CAN do is assert the shape
    # that must never appear if the scope is right -- so widening or dropping that
    # WHERE is caught here rather than discovered in a tracked file.
    region = data_region(artifact_text)
    for table, rules in sorted(forbidden.items()):
        for label, pattern in sorted((rules or {}).items()):
            try:
                found = re.search(pattern, region)
            except re.error as exc:
                failures.append(f"UNUSABLE SCOPE PATTERN for {table} ({label}): {exc}")
                continue
            if found:
                failures.append(
                    f"CARRIED-SUBSET SCOPE BREACHED: {table} ({label})\n"
                    f"    The artifact contains {found.group(0)!r}, which the scope for this\n"
                    f"    partially-carried table forbids. Its render in bin/schema-snapshot.sh\n"
                    f"    has widened past what {rel_config} says it carries.")

    return failures


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: snapshot-seed-coverage.py <repo-root> <candidate-snapshot>\n")
        return 64
    repo, artifact_path = argv[1], argv[2]
    with open(artifact_path, encoding="utf-8", errors="replace") as handle:
        artifact = handle.read()
    try:
        failures = check(repo, artifact)
    except (LookupError, ValueError, OSError, KeyError) as exc:
        sys.stderr.write(f"schema-snapshot: seed-coverage check could not run: {exc}\n")
        return 1
    if not failures:
        return 0
    sys.stderr.write(
        "schema-snapshot: REFUSING to write a snapshot that cannot rebuild its own database.\n"
        "The applied-migration ledger below would tell a rebuild these migrations are done,\n"
        "so their seed rows never replay. Nothing has been written.\n\n")
    for failure in failures:
        sys.stderr.write("  " + failure.replace("\n", "\n  ") + "\n\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
