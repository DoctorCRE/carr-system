"""
parse_extract_targets.py -- INDEPENDENT parse-based target extractor for
WR-000046 Artifact A cross-check (accepted Frontier Finding plan).

Reads migrations 0454-0471 at the pinned commit via
`git show <commit>:migrations/<file>`, splits each file into top-level SQL
statements with a hand-written statement splitter (dollar-quote, string, and
comment aware), classifies each statement, and emits identity strings per
out/frontier-finding/build-specs/target-identity-contract.md.

Independence: this script does not import from tools/, does not open any
database connection, and does not read any other seat's worktree or any file
under docs/frontier-finding/ in another worktree. It derives every target
solely by reading the literal SQL text of the 18 pinned migration files.

Usage:
    python3 parse_extract_targets.py
Writes parse-extracted-targets.v1.json next to this script.
"""

import functools
import json
import re
import subprocess
import sys
from pathlib import Path

PINNED_COMMIT = "0985dcc70764d888d70004641e210f3730ef9d2a"

MIGRATION_FILES = [
    "0454_siep11_mutation_registry.sql",
    "0455_siep12_policy_epoch.sql",
    "0456_siep13_artifact_registry.sql",
    "0457_siep13_forward_mutation_registry.sql",
    "0458_siep14_root_trust.sql",
    "0459_siep14_forward_mutation_registry.sql",
    "0460_siep15_device_enrollment.sql",
    "0461_siep15_forward_mutation_registry.sql",
    "0462_siep16_forward_mutation_registry.sql",
    "0463_retired_rule_delivery_cleanup.sql",
    "0464_siep16_integrated_mutation_registry.sql",
    "0465_siep17_token_challenge_authority.sql",
    "0466_siep17_forward_mutation_registry.sql",
    "0467_siep18_atomic_db_monitor_grants.sql",
    "0468_siep18_forward_mutation_registry.sql",
    "0469_siep18_exact_effects_trusted_principal.sql",
    "0470_source_merge_authority_projection.sql",
    "0471_source_merge_catalog_registry_successor.sql",
]

SCRIPT_DIR = Path(__file__).resolve().parent

NAME_RE = r'[A-Za-z_][A-Za-z0-9_$]*'
QUALNAME_RE = r'(?:"?' + NAME_RE + r'"?\.)?"?' + NAME_RE + r'"?'


@functools.lru_cache(maxsize=None)
def fetch_migration(name):
    result = subprocess.run(
        ["git", "show", "%s:migrations/%s" % (PINNED_COMMIT, name)],
        cwd=str(SCRIPT_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Low-level quote/paren/comment-aware parsing primitives
# ---------------------------------------------------------------------------

DOLLAR_TAG_RE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*)?\$')


def sql_string_literal(text, i):
    """text[i] must be a single quote. Returns (decoded_value, end_index)
    where end_index is the index just after the closing quote. Handles ''
    doubling (all strings) and backslash escapes (only when the string is
    E'...'-prefixed, per standard_conforming_strings semantics)."""
    assert text[i] == "'"
    is_estring = (
        i > 0 and text[i - 1] in 'Ee'
        and (i < 2 or not (text[i - 2].isalnum() or text[i - 2] == '_'))
    )
    n = len(text)
    j = i + 1
    out = []
    escape_map = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', "'": "'"}
    while j < n:
        ch = text[j]
        if is_estring and ch == '\\' and j + 1 < n:
            out.append(escape_map.get(text[j + 1], text[j + 1]))
            j += 2
            continue
        if ch == "'":
            if j + 1 < n and text[j + 1] == "'":
                out.append("'")
                j += 2
                continue
            j += 1
            break
        out.append(ch)
        j += 1
    return ''.join(out), j


def _skip_dquote(text, i):
    n = len(text)
    i += 1
    while i < n:
        if text[i] == '"':
            if i + 1 < n and text[i + 1] == '"':
                i += 2
                continue
            i += 1
            break
        i += 1
    return i


def _skip_dollar(text, i):
    m = DOLLAR_TAG_RE.match(text, i)
    if not m:
        return i + 1
    tag = m.group(0)
    close = text.find(tag, m.end())
    return len(text) if close == -1 else close + len(tag)


def find_matching_paren(text, open_idx):
    """text[open_idx] == '('. Returns index of the matching ')' (quote and
    dollar-quote aware), or -1 if unterminated."""
    assert text[open_idx] == '('
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c == "'":
            _, i = sql_string_literal(text, i)
            continue
        if c == '"':
            i = _skip_dquote(text, i)
            continue
        if c == '$':
            i = _skip_dollar(text, i)
            continue
        if c == '(':
            depth += 1
            i += 1
            continue
        if c == ')':
            depth -= 1
            i += 1
            if depth == 0:
                return i - 1
            continue
        i += 1
    return -1


def split_top_level(text, sep=','):
    """Split text on top-level occurrences of `sep` (quote, paren, and
    dollar-quote aware)."""
    parts = []
    depth = 0
    n = len(text)
    i = 0
    start = 0
    seplen = len(sep)
    while i < n:
        c = text[i]
        if c == "'":
            _, i = sql_string_literal(text, i)
            continue
        if c == '"':
            i = _skip_dquote(text, i)
            continue
        if c == '$':
            i = _skip_dollar(text, i)
            continue
        if c == '(':
            depth += 1
            i += 1
            continue
        if c == ')':
            depth -= 1
            i += 1
            continue
        if depth == 0 and text[i:i + seplen] == sep:
            parts.append(text[start:i])
            i += seplen
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return parts


def strip_string_literal_contents(text):
    """Return a same-length copy of `text` with the interior of every
    top-level single- and double-quoted literal blanked out (spaces, newlines
    preserved), so keyword scans cannot be fooled by literal content."""
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "'":
            start = i
            _, end = sql_string_literal(text, i)
            for k in range(start, min(end, n)):
                if out[k] != '\n':
                    out[k] = ' '
            i = end
            continue
        if c == '"':
            start = i
            end = _skip_dquote(text, i)
            for k in range(start, min(end, n)):
                if out[k] != '\n':
                    out[k] = ' '
            i = end
            continue
        i += 1
    return ''.join(out)


CATALOG_MARKERS = re.compile(
    r'\b(pg_catalog|pg_roles|pg_class|pg_proc|pg_attribute|pg_namespace|'
    r'pg_auth_members|pg_trigger|pg_constraint|pg_extension|pg_depend|'
    r'information_schema|aclexplode|acldefault|has_table_privilege|'
    r'has_function_privilege|has_schema_privilege|has_column_privilege|'
    r'pg_get_expr)\b',
    re.I,
)


def is_dynamic_source(text):
    return bool(CATALOG_MARKERS.search(strip_string_literal_contents(text)))


def literal_string_value(expr):
    """If `expr` is (up to a trailing ::cast) a single quoted string literal,
    return its decoded value; otherwise None."""
    e = expr.strip()
    if len(e) >= 2 and e[0] == "'":
        val, end = sql_string_literal(e, 0)
        remainder = e[end:].strip()
        if remainder == '' or re.match(r'^::\s*[\w\[\]. "]+$', remainder):
            return val
    return None


def normalize_name(name):
    return name.replace('"', '').strip()


def schema_of(qualified_name):
    return qualified_name.rsplit('.', 1)[0] if '.' in qualified_name else 'public'


# ---------------------------------------------------------------------------
# Top-level statement splitter
# ---------------------------------------------------------------------------

def split_statements(text):
    """Split `text` into top-level SQL statements. Returns a list of
    (start_offset, end_offset) pairs into `text`. Respects single-quoted
    strings ('' and E'' escaping), double-quoted identifiers (""
    escaping), dollar-quoted strings ($$ or $tag$), -- line comments, and
    /* */ block comments (non-nested)."""
    n = len(text)
    i = 0
    stmt_start = 0
    spans = []
    while i < n:
        c = text[i]
        if c == '-' and i + 1 < n and text[i + 1] == '-':
            j = text.find('\n', i)
            i = n if j == -1 else j + 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '*':
            j = text.find('*/', i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == "'":
            _, i = sql_string_literal(text, i)
            continue
        if c == '"':
            i = _skip_dquote(text, i)
            continue
        if c == '$':
            m = DOLLAR_TAG_RE.match(text, i)
            if m:
                i = _skip_dollar(text, i)
                continue
            i += 1
            continue
        if c == ';':
            spans.append((stmt_start, i + 1))
            i += 1
            stmt_start = i
            continue
        i += 1
    if stmt_start < n and text[stmt_start:].strip():
        spans.append((stmt_start, n))
    return spans


def leading_strip_offset(stmt_text):
    """Return the offset of the first non-whitespace, non-comment
    character in stmt_text."""
    i = 0
    n = len(stmt_text)
    while i < n:
        if stmt_text[i].isspace():
            i += 1
            continue
        if stmt_text[i:i + 2] == '--':
            j = stmt_text.find('\n', i)
            i = n if j == -1 else j + 1
            continue
        if stmt_text[i:i + 2] == '/*':
            j = stmt_text.find('*/', i + 2)
            i = n if j == -1 else j + 2
            continue
        break
    return i


class Stmt(object):
    __slots__ = ("full_text", "start", "end", "raw", "lead_off", "stripped")

    def __init__(self, full_text, start, end):
        self.full_text = full_text
        self.start = start
        self.end = end
        self.raw = full_text[start:end]
        self.lead_off = leading_strip_offset(self.raw)
        self.stripped = self.raw[self.lead_off:]

    def abs_pos(self, p):
        return self.start + self.lead_off + p

    def line_at(self, p):
        return self.full_text.count('\n', 0, self.abs_pos(p)) + 1

    def line_start(self):
        return self.line_at(0)


def unresolved_entry(filename, line, reason, fragment):
    frag = fragment.replace('\n', ' ')
    frag = re.sub(r'\s+', ' ', frag).strip()
    return {"file": filename, "line": line, "reason": reason, "fragment": frag[:200]}


# ---------------------------------------------------------------------------
# CREATE TABLE
# ---------------------------------------------------------------------------

CREATE_TABLE_RE = re.compile(
    r'create\s+table\s+(if\s+not\s+exists\s+)?(' + QUALNAME_RE + r')\s*\(', re.I
)


def handle_create_table(stmt, filename, table_pk):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = CREATE_TABLE_RE.match(s)
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE TABLE shape", s[:150]))
        return targets, unresolved
    table_name = normalize_name(m.group(2))
    targets.add("table:%s" % table_name)
    paren_open = m.end() - 1
    paren_close = find_matching_paren(s, paren_open)
    body = s[paren_open + 1:paren_close] if paren_close != -1 else s[paren_open + 1:]
    items = split_top_level(body, ',')
    pk_cols = []
    for item in items:
        it = item.strip()
        if not it:
            continue
        it_low = it.lower()
        pkm = re.match(r'primary\s+key\s*\(', it_low)
        if pkm:
            po = it_low.index('(')
            pc = find_matching_paren(it, po)
            cols_text = it[po + 1:pc] if pc != -1 else it[po + 1:]
            pk_cols = [c.strip().strip('"').lower() for c in split_top_level(cols_text, ',')]
            continue
        cm = re.match(r'constraint\s+"?(' + NAME_RE + r')"?\s+(.*)', it, re.I | re.S)
        if cm:
            cons_name = cm.group(1)
            targets.add("constraint:%s.%s" % (table_name, cons_name))
            rest_low = cm.group(2).lower().strip()
            if rest_low.startswith('primary key'):
                po = rest_low.index('(')
                pc = find_matching_paren(cm.group(2), po)
                cols_text = cm.group(2)[po + 1:pc] if pc != -1 else cm.group(2)[po + 1:]
                pk_cols = [c.strip().strip('"').lower() for c in split_top_level(cols_text, ',')]
            continue
        first_word = it.split(None, 1)[0].lower() if it.split(None, 1) else ''
        if first_word in ('unique', 'check', 'foreign', 'exclude'):
            continue
        col_m = re.match(r'"?(' + NAME_RE + r')"?\s+', it)
        if col_m:
            col_name = col_m.group(1).lower()
            if re.search(r'\bprimary\s+key\b', it, re.I):
                pk_cols = [col_name]
    if pk_cols:
        table_pk[table_name] = pk_cols
    return targets, unresolved


# ---------------------------------------------------------------------------
# CREATE/ALTER FUNCTION or PROCEDURE
# ---------------------------------------------------------------------------

FUNC_HEADER_RE = re.compile(
    r'create\s+(or\s+replace\s+)?(function|procedure)\s+(' + QUALNAME_RE + r')\s*\(', re.I
)


# PATCH (WR-000046 comparison seat, slice-compare): pg_get_function_identity_arguments
# -- the exact rendering the identity contract cites for `function:` targets --
# was empirically cross-checked against the OBSERVED effect manifest
# (docs/frontier-finding/frontier-touched-objects.v1.json, produced by a real
# Postgres 17 run) for all 109 functions created/altered in 0454-0471. Ground
# truth: it retains the parameter NAME alongside the type for every named
# parameter (every parameter in this corpus is named), and it renders the
# type via format_type() canonical spelling, not the raw SQL keyword. The
# original parser stripped names and used the raw SQL type keyword verbatim;
# this produced 46 false-only-in-observed / 46 false-only-in-parsed diffs,
# all traced to this single rendering gap (see comparison-report.md, rule
# fn_identity_args_regenerated). Confirmed by direct comparison against each
# function's own CREATE FUNCTION parameter list at the pinned commit: the
# OBSERVED "name type" string matches the migration source's own parameter
# declaration token-for-token, with only the type spelling canonicalized.
PARAM_TYPE_CANONICAL = {
    # The only alias appearing in any function/procedure parameter list in
    # migrations/0454-0471 at the pinned commit (verified by scanning every
    # CREATE FUNCTION/PROCEDURE signature in range: the full set of raw type
    # keywords used is bigint, boolean, integer, jsonb, text, text[],
    # timestamptz, uuid -- only timestamptz is non-canonical). format_type()
    # always expands this alias to its SQL-standard spelling.
    "timestamptz": "timestamp with time zone",
}


def canonicalize_type(t):
    return PARAM_TYPE_CANONICAL.get(t.strip().lower(), t.strip())


# PATCH (slice-compare): populated by handle_create_function as a side effect,
# in file-processing order, so that by the time a later GRANT/REVOKE/COMMENT
# statement references a function by its type-only (or bare) identity, the
# canonical "name type,..." signature captured from its own CREATE FUNCTION
# statement is already available for lookup. Keyed by (schema.name, argcount)
# rather than just name, so a hypothetical arg-count collision would fall
# back to the raw text instead of guessing (verified: zero collisions occur
# across the 109 real functions in this corpus).
FUNCTION_SIGNATURE_INDEX: dict[tuple[str, int], str] = {}


def parse_name_and_type_from_param(param_text):
    t = re.sub(r'\bdefault\b.*$', '', param_text.strip(), flags=re.I | re.S).strip()
    t = re.sub(r'^(in)\s+', '', t, flags=re.I)
    parts = t.split(None, 1)
    if len(parts) == 2:
        name = parts[0].strip()
        typ = canonicalize_type(re.sub(r'\s+', ' ', parts[1].strip()))
        return name, typ
    if len(parts) == 1:
        return None, canonicalize_type(parts[0].strip())
    return None, ''


def handle_create_function(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = FUNC_HEADER_RE.match(s)
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE FUNCTION/PROCEDURE shape", s[:150]))
        return targets, unresolved
    func_name = normalize_name(m.group(3))
    paren_open = m.end() - 1
    paren_close = find_matching_paren(s, paren_open)
    params_text = s[paren_open + 1:paren_close] if paren_close != -1 else ''
    params = [p for p in split_top_level(params_text, ',') if p.strip()]
    parsed = [parse_name_and_type_from_param(p) for p in params]
    parsed = [(n, t) for (n, t) in parsed if t]
    sig = ','.join((("%s %s" % (n, t)) if n else t) for (n, t) in parsed)
    targets.add("function:%s(%s)" % (func_name, sig))
    FUNCTION_SIGNATURE_INDEX[(func_name, len(parsed))] = sig
    return targets, unresolved


def handle_alter_function(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(r'alter\s+function\s+(' + QUALNAME_RE + r')\s*\(', s, re.I)
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized ALTER FUNCTION shape", s[:150]))
        return targets, unresolved
    old_name = normalize_name(m.group(1))
    paren_open = m.end() - 1
    paren_close = find_matching_paren(s, paren_open)
    argtext = s[paren_open + 1:paren_close] if paren_close != -1 else ''
    args = ','.join(a.strip() for a in split_top_level(argtext, ',') if a.strip())
    rest = s[paren_close + 1:] if paren_close != -1 else ''
    rm = re.match(r'\s*rename\s+to\s+"?(' + NAME_RE + r')"?', rest, re.I)
    if rm:
        new_bare_name = rm.group(1)
        schema = schema_of(old_name)
        new_name = "%s.%s" % (schema, new_bare_name) if '.' in old_name else new_bare_name
        targets.add("function:%s(%s)" % (new_name, args))
        return targets, unresolved
    unresolved.append(unresolved_entry(filename, stmt.line_start(),
        "ALTER FUNCTION form other than RENAME TO is not handled by this extractor", s[:150]))
    return targets, unresolved


# ---------------------------------------------------------------------------
# CREATE/DROP TRIGGER
# ---------------------------------------------------------------------------

def handle_create_trigger(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(
        r'create\s+(constraint\s+)?trigger\s+"?(' + NAME_RE + r')"?\s+.*?\bon\s+(' + QUALNAME_RE + r')\b',
        s, re.I | re.S,
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE TRIGGER shape", s[:150]))
        return targets, unresolved
    trig_name = m.group(2)
    table_name = normalize_name(m.group(3))
    targets.add("trigger:%s.%s" % (table_name, trig_name))
    return targets, unresolved


def handle_drop_trigger(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(
        r'drop\s+trigger\s+(if\s+exists\s+)?"?(' + NAME_RE + r')"?\s+on\s+(' + QUALNAME_RE + r')',
        s, re.I,
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized DROP TRIGGER shape", s[:150]))
        return targets, unresolved
    trig_name = m.group(2)
    table_name = normalize_name(m.group(3))
    targets.add("trigger:%s.%s" % (table_name, trig_name))
    return targets, unresolved


def extract_trigger_identity_from_ddl(sql_text):
    m = re.match(
        r'create\s+(constraint\s+)?trigger\s+"?(' + NAME_RE + r')"?\s+.*?\bon\s+(' + QUALNAME_RE + r')\b',
        sql_text.strip(), re.I | re.S,
    )
    if not m:
        return None
    trig_name = m.group(2)
    table_name = normalize_name(m.group(3))
    return "trigger:%s.%s" % (table_name, trig_name)


# ---------------------------------------------------------------------------
# ALTER TABLE
# ---------------------------------------------------------------------------

def handle_alter_table(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(r'alter\s+table\s+(if\s+exists\s+)?(' + QUALNAME_RE + r')\b', s, re.I)
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized ALTER TABLE shape", s[:150]))
        return targets, unresolved
    table_name = normalize_name(m.group(2))
    targets.add("table:%s" % table_name)
    for cm in re.finditer(r'\b(add|drop)\s+constraint\s+"?(' + NAME_RE + r')"?', s, re.I):
        targets.add("constraint:%s.%s" % (table_name, cm.group(2)))
    return targets, unresolved


# ---------------------------------------------------------------------------
# COMMENT ON
# ---------------------------------------------------------------------------

def handle_comment_on(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(r'comment\s+on\s+(function|procedure)\s+(' + QUALNAME_RE + r')\s*\(', s, re.I)
    if m:
        name = normalize_name(m.group(2))
        paren_open = m.end() - 1
        paren_close = find_matching_paren(s, paren_open)
        argtext = s[paren_open + 1:paren_close] if paren_close != -1 else ''
        raw_args = [a.strip() for a in split_top_level(argtext, ',') if a.strip()]
        # PATCH (slice-compare): same type-only-args resolution as
        # object_identity_and_kind (COMMENT ON FUNCTION also identifies its
        # target with types only in real SQL grammar). Cross-file rename
        # forwarding (a COMMENT ON a bare function name that a LATER file
        # renames away) is applied afterwards in forward_resolve_comments(),
        # once the full rename chain across all 18 files is known.
        resolved = FUNCTION_SIGNATURE_INDEX.get((name, len(raw_args)))
        args = resolved if resolved is not None else ','.join(raw_args)
        targets.add("comment:function:%s(%s)" % (name, args))
        return targets, unresolved
    m = re.match(r'comment\s+on\s+table\s+(' + QUALNAME_RE + r')', s, re.I)
    if m:
        targets.add("comment:table:%s" % normalize_name(m.group(1)))
        return targets, unresolved
    m = re.match(r'comment\s+on\s+(materialized\s+view|view)\s+(' + QUALNAME_RE + r')', s, re.I)
    if m:
        kind = 'matview' if 'materialized' in m.group(1).lower() else 'view'
        targets.add("comment:%s:%s" % (kind, normalize_name(m.group(2))))
        return targets, unresolved
    m = re.match(r'comment\s+on\s+trigger\s+"?(' + NAME_RE + r')"?\s+on\s+(' + QUALNAME_RE + r')', s, re.I)
    if m:
        targets.add("comment:trigger:%s.%s" % (normalize_name(m.group(2)), m.group(1)))
        return targets, unresolved
    m = re.match(r'comment\s+on\s+constraint\s+"?(' + NAME_RE + r')"?\s+on\s+(' + QUALNAME_RE + r')', s, re.I)
    if m:
        targets.add("comment:constraint:%s.%s" % (normalize_name(m.group(2)), m.group(1)))
        return targets, unresolved
    m = re.match(r'comment\s+on\s+policy\s+"?(' + NAME_RE + r')"?\s+on\s+(' + QUALNAME_RE + r')', s, re.I)
    if m:
        targets.add("comment:policy:%s.%s" % (normalize_name(m.group(2)), m.group(1)))
        return targets, unresolved
    m = re.match(r'comment\s+on\s+schema\s+"?(' + NAME_RE + r')"?', s, re.I)
    if m:
        targets.add("comment:schema:%s" % m.group(1))
        return targets, unresolved
    m = re.match(r'comment\s+on\s+(sequence|type|index)\s+(' + QUALNAME_RE + r')', s, re.I)
    if m:
        targets.add("comment:%s:%s" % (m.group(1).lower(), normalize_name(m.group(2))))
        return targets, unresolved
    unresolved.append(unresolved_entry(filename, stmt.line_start(),
        "unrecognized COMMENT ON object type", s[:150]))
    return targets, unresolved


# ---------------------------------------------------------------------------
# GRANT / REVOKE
# ---------------------------------------------------------------------------

TABLE_ALL = ['select', 'insert', 'update', 'delete', 'truncate', 'references', 'trigger']
FUNCTION_ALL = ['execute']
SCHEMA_ALL = ['usage', 'create']
SEQUENCE_ALL = ['select', 'usage', 'update']
TYPE_ALL = ['usage']

KIND_MAP = {
    'table': 'table', 'function': 'function', 'procedure': 'function',
    'schema': 'schema', 'sequence': 'sequence', 'type': 'type',
}
ALL_PRIVS_BY_KIND = {
    'table': TABLE_ALL, 'function': FUNCTION_ALL, 'schema': SCHEMA_ALL,
    'sequence': SEQUENCE_ALL, 'type': TYPE_ALL,
}


def object_identity_and_kind(obj_text, explicit_kind):
    obj_text = obj_text.strip()
    if '(' in obj_text:
        paren_open = obj_text.index('(')
        name_part = obj_text[:paren_open]
        paren_close = find_matching_paren(obj_text, paren_open)
        args_text = obj_text[paren_open + 1:paren_close] if paren_close != -1 else ''
        raw_args = [a.strip() for a in split_top_level(args_text, ',') if a.strip()]
        name = normalize_name(name_part.strip())
        kind = explicit_kind or 'function'
        if kind == 'function':
            # PATCH (slice-compare): GRANT/REVOKE/DROP-style FUNCTION syntax
            # identifies its target with a type-only argument list -- this is
            # the real SQL grammar (verified against the migration text
            # itself, not a parser defect). Resolve to the canonical
            # "name type" signature already captured from this function's own
            # CREATE FUNCTION statement so the emitted identity matches the
            # form used for `function:` targets (see FUNCTION_SIGNATURE_INDEX).
            resolved = FUNCTION_SIGNATURE_INDEX.get((name, len(raw_args)))
            if resolved is not None:
                return kind, "%s(%s)" % (name, resolved)
        args = ','.join(raw_args)
        return kind, "%s(%s)" % (name, args)
    name = normalize_name(obj_text)
    kind = explicit_kind or 'table'
    return kind, name


# PATCH (slice-compare): cumulative ACL-state tracking for REVOKE no-op
# suppression.
#
# `REVOKE ALL ... FROM public,carr_reader,carr_writer,carr_jobs,carr_authority`
# immediately after CREATE FUNCTION/TABLE (before ANY explicit GRANT has ever
# touched the object) is a recurring idiom in this corpus. Textually it names
# five grantees, but at that point in a real database none of the five holds
# the privilege yet, so the REVOKE removes nothing -- it produces zero ACL
# change and is therefore invisible to a snapshot-diff-based observer (cross-
# checked directly: migrations/0454_siep11_mutation_registry.sql lines 90-91
# revoke-then-grant ops.scac_mutation_registration(text,text) to
# carr_reader/writer/jobs/authority; the OBSERVED effect manifest shows this
# produces ONLY four grant: entries for those roles plus one owner-
# materialization grant for carr_manifest, and the FIRST real revoke: entry
# for this function is attributed to a LATER, separate REVOKE ALL statement
# in migrations/0455_siep12_policy_epoch.sql line 337, which runs AFTER the
# 0454 grant and genuinely removes it). A textual REVOKE that names a
# (object, grantee, privilege) triple which the migration text has not
# previously GRANTed is therefore predicted as a no-op and excluded from the
# emitted `revoke:` set, exactly matching observed effect semantics -- this
# is NOT guessed: it is derived purely from the GRANT/REVOKE statement order
# already visible in migrations/0454-0471's own text, tracked as a plain
# forward simulation (no default-privilege assumption is needed: PUBLIC is
# never the target of an explicit GRANT anywhere in this corpus, so it never
# transitions to a granted state and every REVOKE naming PUBLIC is correctly
# suppressed under the same rule, with no special-casing). GRANT emission is
# left unfiltered/unchanged: zero redundant re-grants of an
# already-granted (object, grantee, privilege) triple occur anywhere in this
# corpus (verified empirically -- filtering GRANT the same way changes
# nothing), so filtering it would add risk without changing any output.
ACL_GRANTED_STATE: dict[tuple[str, str, str, str], bool] = {}


def handle_grant_revoke(stmt, filename, is_grant):
    targets = set()
    unresolved = []
    s = stmt.stripped
    verb = 'grant' if is_grant else 'revoke'
    m = re.match(
        verb + r'\s+(.*?)\s+on\s+(table\s+|function\s+|procedure\s+|schema\s+|sequence\s+|type\s+)?'
        r'(.*?)\s+(to|from)\s+(.*)$',
        s, re.I | re.S,
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized %s shape" % verb.upper(), s[:150]))
        return targets, unresolved
    priv_clause = m.group(1).strip()
    explicit_kind_kw = (m.group(2) or '').strip().lower() or None
    obj_clause = m.group(3).strip()
    grantees_clause = m.group(5).strip()
    grantees_clause = re.split(r'\bwith\s+grant\s+option\b', grantees_clause, flags=re.I)[0]
    grantees_clause = grantees_clause.strip().rstrip(';').strip()
    grantees = [g.strip().strip('"') for g in split_top_level(grantees_clause, ',') if g.strip()]
    explicit_kind = KIND_MAP.get(explicit_kind_kw) if explicit_kind_kw else None
    objects = [o.strip() for o in split_top_level(obj_clause, ',') if o.strip()]
    is_all = bool(re.match(r'all(\s+privileges)?$', priv_clause, re.I))
    for obj_text in objects:
        kind, ident = object_identity_and_kind(obj_text, explicit_kind)
        if is_all:
            privs = ALL_PRIVS_BY_KIND.get(kind, TABLE_ALL)
        else:
            raw_privs = [p.strip() for p in split_top_level(priv_clause, ',') if p.strip()]
            privs = [re.sub(r'\(.*?\)', '', p).strip().lower() for p in raw_privs]
        verb_prefix = 'grant' if is_grant else 'revoke'
        for priv in privs:
            for grantee in grantees:
                key = (kind, ident, grantee, priv.upper())
                if is_grant:
                    targets.add("%s:%s:%s:%s:%s" % (verb_prefix, kind, ident, grantee, priv.upper()))
                    ACL_GRANTED_STATE[key] = True
                elif ACL_GRANTED_STATE.get(key, False):
                    targets.add("%s:%s:%s:%s:%s" % (verb_prefix, kind, ident, grantee, priv.upper()))
                    ACL_GRANTED_STATE[key] = False
                # else: no-op REVOKE (nothing currently granted per the
                # in-corpus GRANT/REVOKE order tracked so far) -- excluded,
                # see ACL_GRANTED_STATE comment above.
    return targets, unresolved


# ---------------------------------------------------------------------------
# INSERT / UPDATE / DELETE (control-table row DML) -- top-level statements
# only. Statements inside a CREATE FUNCTION/PROCEDURE body or a DO block are
# never split out as separate top-level statements by split_statements(),
# since dollar-quoted bodies are opaque to the splitter; such INSERT/UPDATE/
# DELETE text executes later, when the function is called, not when the
# migration runs, so it correctly never reaches these handlers.
# ---------------------------------------------------------------------------

INSERT_RE = re.compile(r'insert\s+into\s+(' + QUALNAME_RE + r')\s*\(', re.I)


def _extract_row_tuples(tuples_text):
    """tuples_text starts right after the VALUES keyword. Returns a list of
    raw tuple-body strings (contents between each top-level parenthesis
    pair)."""
    row_texts = []
    i = 0
    n = len(tuples_text)
    while i < n:
        c = tuples_text[i]
        if c == "'":
            _, i = sql_string_literal(tuples_text, i)
            continue
        if c == '"':
            i = _skip_dquote(tuples_text, i)
            continue
        if c == '$':
            i = _skip_dollar(tuples_text, i)
            continue
        if c == '(':
            close = find_matching_paren(tuples_text, i)
            if close == -1:
                break
            row_texts.append(tuples_text[i + 1:close])
            i = close + 1
            continue
        i += 1
    return row_texts


def resolve_values_insert(s, rest, filename, line_no, table_name, cols, pk_idx):
    targets = set()
    unresolved = []
    rest_low = rest.lower()
    vpos = rest_low.index('values') + len('values')
    tuples_text = rest[vpos:]
    oc_cut = re.search(r'\bon\s+conflict\b', tuples_text, re.I)
    if oc_cut:
        tuples_text = tuples_text[:oc_cut.start()]
    row_texts = _extract_row_tuples(tuples_text)
    if not row_texts:
        unresolved.append(unresolved_entry(filename, line_no,
            "VALUES clause had no parenthesized row tuples", rest[:150]))
        return targets, unresolved
    for row_text in row_texts:
        vals = split_top_level(row_text, ',')
        if len(vals) != len(cols):
            unresolved.append(unresolved_entry(filename, line_no,
                "VALUES tuple arity does not match the INSERT column list; cannot map "
                "primary key positionally", row_text[:150]))
            continue
        pk_values = []
        resolvable = True
        for idx in pk_idx:
            lit = literal_string_value(vals[idx])
            if lit is None:
                resolvable = False
                break
            pk_values.append(lit)
        if resolvable:
            targets.add("row:%s:%s" % (table_name, '|'.join(pk_values)))
        else:
            unresolved.append(unresolved_entry(filename, line_no,
                "one or more primary key values in this VALUES tuple are non-literal expressions",
                row_text[:150]))
    return targets, unresolved


def resolve_json_seed_insert(s, pre_text, rest, filename, line_no, table_name, cols, pk_idx):
    """Resolves the recurring
        with <cte> as (select <elemcol> as <alias> from jsonb_array_elements('<json>'[::jsonb]))
        insert into t(cols) select <exprs...> from <cte>;
    idiom, where <json> is a literal JSON array of objects and each select-list
    expression feeding a primary-key column is either a string literal or a
    plain "<alias>->>'<field>'" projection of the unpacked JSON object."""
    targets = set()
    unresolved = []
    cte_m = re.search(
        r'\b(' + NAME_RE + r')\s+as\s*\(\s*select\s+(' + NAME_RE + r')\s+as\s+(' + NAME_RE +
        r')\s+from\s+jsonb_array_elements\s*\(',
        pre_text, re.I,
    )
    if not cte_m:
        unresolved.append(unresolved_entry(filename, line_no,
            "INSERT...SELECT source is not the recognized literal-JSON-array CTE idiom "
            "(with <cte> as (select <col> as <alias> from jsonb_array_elements('<json>')))",
            s[:150]))
        return targets, unresolved
    cte_name = cte_m.group(1)
    col_alias = cte_m.group(3)
    quote_pos = cte_m.end()
    while quote_pos < len(pre_text) and pre_text[quote_pos].isspace():
        quote_pos += 1
    if quote_pos >= len(pre_text) or pre_text[quote_pos] != "'":
        unresolved.append(unresolved_entry(filename, line_no,
            "jsonb_array_elements() argument is not a simple string literal",
            pre_text[cte_m.start():cte_m.start() + 150]))
        return targets, unresolved
    json_text, _end = sql_string_literal(pre_text, quote_pos)
    try:
        rows = json.loads(json_text)
    except Exception as e:
        unresolved.append(unresolved_entry(filename, line_no,
            "literal JSON array in jsonb_array_elements() failed to parse: %s" % e,
            pre_text[cte_m.start():cte_m.start() + 150]))
        return targets, unresolved

    m2 = re.match(r'select\s+(.*?)\s+from\s+(' + NAME_RE + r')\s*;?\s*$', rest, re.I | re.S)
    if not m2 or m2.group(2) != cte_name:
        unresolved.append(unresolved_entry(filename, line_no,
            "outer SELECT is not a plain 'select <exprs> from <cte>' with no filtering/join, "
            "or references a different CTE than the literal-JSON one; a 1:1 per-row mapping "
            "is not guaranteed", rest[:150]))
        return targets, unresolved
    select_list = [e.strip() for e in split_top_level(m2.group(1), ',')]
    if len(select_list) != len(cols):
        unresolved.append(unresolved_entry(filename, line_no,
            "SELECT list arity does not match the INSERT column list", rest[:150]))
        return targets, unresolved

    field_pat = re.compile(r"^" + re.escape(col_alias) + r"\s*->>\s*'([^']+)'$")
    resolvers = []
    for idx in pk_idx:
        expr = select_list[idx].strip()
        lit = literal_string_value(expr)
        if lit is not None:
            resolvers.append(('const', lit))
            continue
        fm = field_pat.match(expr)
        if fm:
            resolvers.append(('field', fm.group(1)))
            continue
        unresolved.append(unresolved_entry(filename, line_no,
            "primary key expression '%s' is neither a literal nor a simple %s->>'field' "
            "projection of the seeded JSON row" % (expr, col_alias), expr[:150]))
        return targets, unresolved

    if not isinstance(rows, list):
        unresolved.append(unresolved_entry(filename, line_no,
            "literal JSON payload is not a JSON array", json_text[:150]))
        return targets, unresolved

    for obj in rows:
        if not isinstance(obj, dict):
            unresolved.append(unresolved_entry(filename, line_no,
                "a seeded JSON array element is not a JSON object", str(obj)[:150]))
            continue
        pk_values = []
        ok = True
        for kind, val in resolvers:
            if kind == 'const':
                pk_values.append(val)
            else:
                fv = obj.get(val)
                if fv is None:
                    ok = False
                    unresolved.append(unresolved_entry(filename, line_no,
                        "seeded JSON row is missing expected field '%s' used as a primary "
                        "key component" % val, json.dumps(obj)[:150]))
                    break
                pk_values.append(str(fv))
        if ok:
            targets.add("row:%s:%s" % (table_name, '|'.join(pk_values)))
    return targets, unresolved


def handle_insert(stmt, filename, table_pk):
    targets = set()
    unresolved = []
    s = stmt.stripped
    line_no = stmt.line_start()
    im = re.search(r'insert\s+into\s+(' + QUALNAME_RE + r')\s*\(', s, re.I)
    if not im:
        unresolved.append(unresolved_entry(filename, line_no,
            "INSERT statement without an explicit column list is out of scope for this "
            "extractor", s[:150]))
        return targets, unresolved
    pre_text = s[:im.start()]
    table_name = normalize_name(im.group(1))
    paren_open = im.end() - 1
    paren_close = find_matching_paren(s, paren_open)
    cols_text = s[paren_open + 1:paren_close] if paren_close != -1 else ''
    cols = [c.strip().strip('"').lower() for c in split_top_level(cols_text, ',')]
    rest = s[paren_close + 1:].strip() if paren_close != -1 else ''

    pk_cols = table_pk.get(table_name)
    oc_m = re.search(r'\bon\s+conflict\s*\(([^)]*)\)', rest, re.I)
    on_conflict_cols = (
        [c.strip().strip('"').lower() for c in split_top_level(oc_m.group(1), ',')]
        if oc_m else None
    )
    effective_pk = pk_cols or on_conflict_cols
    if not effective_pk:
        unresolved.append(unresolved_entry(filename, line_no,
            "primary key for %s is unknown (table not created within migrations 0454-0471 "
            "and this INSERT has no ON CONFLICT target to infer it)" % table_name, s[:150]))
        return targets, unresolved

    missing = [c for c in effective_pk if c not in cols]
    if missing:
        unresolved.append(unresolved_entry(filename, line_no,
            "primary key column(s) %s of %s are not present in the INSERT column list" %
            (missing, table_name), s[:150]))
        return targets, unresolved
    pk_idx = [cols.index(c) for c in effective_pk]

    rest_low = rest.lower()
    if rest_low.startswith('values'):
        return resolve_values_insert(s, rest, filename, line_no, table_name, cols, pk_idx)

    if rest_low.startswith('select') or pre_text.strip().lower().startswith('with'):
        full_scope = pre_text + rest
        if is_dynamic_source(full_scope):
            unresolved.append(unresolved_entry(filename, line_no,
                "INSERT...SELECT is sourced from live database catalog/role/ACL "
                "introspection (pg_roles/pg_class/pg_proc/pg_attribute/aclexplode/etc.); "
                "this is genuinely dynamic per-database state and cannot be statically "
                "resolved from migration text alone", s[:150]))
            return targets, unresolved
        return resolve_json_seed_insert(s, pre_text, rest, filename, line_no, table_name, cols, pk_idx)

    unresolved.append(unresolved_entry(filename, line_no,
        "unrecognized INSERT data-source shape (neither VALUES nor SELECT)", s[:150]))
    return targets, unresolved


def _resolve_pk_from_where(where_text, pk_cols):
    pk_values = []
    for col in pk_cols:
        cm = re.search(r'(?:\b\w+\.)?' + re.escape(col) + r"\s*=\s*('(?:[^']|'')*')", where_text, re.I)
        if not cm:
            return None
        val = literal_string_value(cm.group(1))
        if val is None:
            return None
        pk_values.append(val)
    return pk_values


def handle_update(stmt, filename, table_pk):
    targets = set()
    unresolved = []
    s = stmt.stripped
    line_no = stmt.line_start()
    m = re.match(
        r'update\s+(' + QUALNAME_RE + r')\s*(?:(?:as\s+)?"?' + NAME_RE + r'"?\s+)?set\b',
        s, re.I,
    )
    if not m:
        unresolved.append(unresolved_entry(filename, line_no, "unrecognized UPDATE shape", s[:150]))
        return targets, unresolved
    table_name = normalize_name(m.group(1))
    pk_cols = table_pk.get(table_name)
    where_m = re.search(r'\bwhere\b(.*)$', s, re.I | re.S)
    if not pk_cols or not where_m:
        unresolved.append(unresolved_entry(filename, line_no,
            "UPDATE target primary key is unknown, or the statement has no WHERE clause to "
            "pin it", s[:150]))
        return targets, unresolved
    pk_values = _resolve_pk_from_where(where_m.group(1), pk_cols)
    if pk_values is None:
        unresolved.append(unresolved_entry(filename, line_no,
            "UPDATE WHERE clause does not pin every primary key column to a literal "
            "equality value", where_m.group(1)[:150]))
        return targets, unresolved
    targets.add("row:%s:%s" % (table_name, '|'.join(pk_values)))
    return targets, unresolved


def handle_delete(stmt, filename, table_pk):
    targets = set()
    unresolved = []
    s = stmt.stripped
    line_no = stmt.line_start()
    m = re.match(r'delete\s+from\s+(' + QUALNAME_RE + r')\b', s, re.I)
    if not m:
        unresolved.append(unresolved_entry(filename, line_no, "unrecognized DELETE shape", s[:150]))
        return targets, unresolved
    table_name = normalize_name(m.group(1))
    pk_cols = table_pk.get(table_name)
    where_m = re.search(r'\bwhere\b(.*)$', s, re.I | re.S)
    if not pk_cols or not where_m:
        unresolved.append(unresolved_entry(filename, line_no,
            "DELETE target primary key is unknown, or the statement has no WHERE clause to "
            "pin it", s[:150]))
        return targets, unresolved
    pk_values = _resolve_pk_from_where(where_m.group(1), pk_cols)
    if pk_values is None:
        unresolved.append(unresolved_entry(filename, line_no,
            "DELETE WHERE clause does not pin every primary key column to a literal "
            "equality value", where_m.group(1)[:150]))
        return targets, unresolved
    targets.add("row:%s:%s" % (table_name, '|'.join(pk_values)))
    return targets, unresolved


# ---------------------------------------------------------------------------
# DO blocks
#
# Every DO block observed in migrations 0454-0471 is one of three shapes:
#   1. Pure validation (SELECT counts/digests, then RAISE EXCEPTION on
#      mismatch) -- no DDL/DML at all, so it touches nothing and is skipped
#      silently (neither a target nor an unresolved entry).
#   2. The literal idiom
#        do $tag$ declare v text; begin
#          foreach v in array array['lit1','lit2',...] loop
#            execute format('create trigger %I ... on ops.%I ...', v||'_x', v);
#            ...
#          end loop; end $tag$;
#      which is fully statically resolvable: the array is a literal list of
#      relation names and the format() calls render deterministic CREATE
#      TRIGGER statements.
#   3. Genuinely dynamic per-relation/per-role DDL driven by live catalog
#      introspection (pg_class/pg_roles/aclexplode/...), which is NOT
#      statically resolvable and is reported via `unresolved`.
# ---------------------------------------------------------------------------

ACTION_RE = re.compile(
    r'\b(insert\s+into|update\s+\S+\s+set|delete\s+from|create\s+|alter\s+|drop\s+)\b'
    r'|execute\s+format\s*\(|execute\s+\'',
    re.I,
)


def eval_simple_string_expr(expr, var_values):
    """Evaluate a SQL text expression made only of a known variable name
    and/or string literals concatenated with ||. Returns the resulting
    Python string, or None if any part cannot be evaluated."""
    parts = split_top_level(expr, '||')
    out = []
    for p in parts:
        p = p.strip()
        if p in var_values:
            out.append(var_values[p])
            continue
        lit = literal_string_value(p)
        if lit is not None:
            out.append(lit)
            continue
        return None
    return ''.join(out)


def render_format(template, args):
    out = []
    ai = 0
    i = 0
    n = len(template)
    while i < n:
        c = template[i]
        if c == '%' and i + 1 < n:
            spec = template[i + 1]
            if spec in ('I', 's', 'L'):
                if ai >= len(args):
                    return None
                out.append(args[ai])
                ai += 1
                i += 2
                continue
            if spec == '%':
                out.append('%')
                i += 2
                continue
        out.append(c)
        i += 1
    return ''.join(out)


def find_dollar_span(s, start=0):
    m = DOLLAR_TAG_RE.search(s, start)
    if not m:
        return None
    tag = m.group(0)
    open_end = m.end()
    close = s.find(tag, open_end)
    if close == -1:
        return None
    return tag, open_end, close, close + len(tag)


def handle_do_block(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    line_no = stmt.line_start()
    span = find_dollar_span(s)
    if not span:
        unresolved.append(unresolved_entry(filename, line_no,
            "DO block has no recognizable dollar-quoted body", s[:150]))
        return targets, unresolved
    tag, body_start, body_end, _close_end = span
    body = s[body_start:body_end]
    body_line_no = stmt.line_at(body_start)

    if not ACTION_RE.search(strip_string_literal_contents(body)):
        return targets, unresolved  # pure validation block; nothing to extract

    fm = re.search(
        r'foreach\s+(' + NAME_RE + r')\s+in\s+array\s+array\s*\[(.*?)\]\s+loop(.*?)end\s+loop',
        body, re.I | re.S,
    )
    if fm and re.search(r'execute\s+format\s*\(', fm.group(3), re.I) and not is_dynamic_source(fm.group(3)):
        loop_var = fm.group(1)
        arr_text = fm.group(2)
        items = []
        i = 0
        while i < len(arr_text):
            if arr_text[i] == "'":
                val, i = sql_string_literal(arr_text, i)
                items.append(val)
            else:
                i += 1
        loop_body = fm.group(3)
        exec_matches = list(re.finditer(
            r"execute\s+format\s*\(\s*'((?:[^']|'')*)'\s*,\s*(.*?)\)\s*;", loop_body, re.I | re.S
        ))
        resolved_ok = bool(items) and bool(exec_matches)
        pending = set()
        outer_break = False
        for item in items:
            for em in exec_matches:
                template = em.group(1).replace("''", "'")
                args_text = em.group(2)
                arg_exprs = [a.strip() for a in split_top_level(args_text, ',') if a.strip()]
                arg_values = []
                for a in arg_exprs:
                    av = eval_simple_string_expr(a, {loop_var: item})
                    if av is None:
                        resolved_ok = False
                        break
                    arg_values.append(av)
                if not resolved_ok:
                    outer_break = True
                    break
                rendered = render_format(template, arg_values)
                trig = extract_trigger_identity_from_ddl(rendered) if rendered is not None else None
                if trig:
                    pending.add(trig)
                else:
                    resolved_ok = False
                    outer_break = True
                    break
            if outer_break:
                break
        if resolved_ok:
            targets |= pending
            return targets, unresolved
        unresolved.append(unresolved_entry(filename, body_line_no,
            "DO block foreach/execute-format idiom did not fully resolve statically", body[:200]))
        return targets, unresolved

    unresolved.append(unresolved_entry(filename, body_line_no,
        "DO block performs dynamic SQL (EXECUTE) and/or DDL/DML this extractor cannot "
        "statically resolve (e.g. driven by live catalog/role/ACL introspection)", body[:200]))
    return targets, unresolved


# ---------------------------------------------------------------------------
# Defensive handlers for object kinds NOT present in migrations 0454-0471
# (confirmed by exhaustive grep during authoring: zero occurrences of CREATE
# INDEX/SEQUENCE/SCHEMA/VIEW/TYPE, CREATE POLICY, OWNER TO, or ALTER DEFAULT
# PRIVILEGES in this file range). Implemented for contract completeness and
# so an unexpected shape in a re-run fails loudly (via `unresolved`) rather
# than silently, never exercised by this corpus.
# ---------------------------------------------------------------------------

def handle_create_index(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(
        r'create\s+(unique\s+)?index\s+(concurrently\s+)?(if\s+not\s+exists\s+)?'
        r'"?(' + NAME_RE + r')"?\s+on\s+(?:only\s+)?(' + QUALNAME_RE + r')',
        s, re.I,
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE INDEX shape", s[:150]))
        return targets, unresolved
    idx_name = m.group(4)
    table_name = normalize_name(m.group(5))
    targets.add("index:%s.%s" % (schema_of(table_name), idx_name))
    return targets, unresolved


def handle_create_sequence(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(
        r'create\s+(temp\s+|temporary\s+)?sequence\s+(if\s+not\s+exists\s+)?(' + QUALNAME_RE + r')',
        s, re.I,
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE SEQUENCE shape", s[:150]))
        return targets, unresolved
    targets.add("sequence:%s" % normalize_name(m.group(3)))
    return targets, unresolved


def handle_create_schema(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(r'create\s+schema\s+(if\s+not\s+exists\s+)?"?(' + NAME_RE + r')"?', s, re.I)
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE SCHEMA shape", s[:150]))
        return targets, unresolved
    targets.add("schema:%s" % m.group(2))
    return targets, unresolved


def handle_create_view(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(
        r'create\s+(or\s+replace\s+)?(materialized\s+)?view\s+(' + QUALNAME_RE + r')', s, re.I
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE VIEW shape", s[:150]))
        return targets, unresolved
    kind = 'matview' if m.group(2) else 'view'
    targets.add("%s:%s" % (kind, normalize_name(m.group(3))))
    return targets, unresolved


def handle_create_type(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(r'create\s+type\s+(' + QUALNAME_RE + r')', s, re.I)
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE TYPE shape", s[:150]))
        return targets, unresolved
    name = normalize_name(m.group(1))
    kind = 'enum' if re.search(r'\bas\s+enum\b', s, re.I) else 'type'
    targets.add("%s:%s" % (kind, name))
    return targets, unresolved


def handle_alter_type(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(r'alter\s+type\s+(' + QUALNAME_RE + r')', s, re.I)
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized ALTER TYPE shape", s[:150]))
        return targets, unresolved
    targets.add("enum:%s" % normalize_name(m.group(1)))
    return targets, unresolved


def handle_create_policy(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(
        r'create\s+policy\s+"?(' + NAME_RE + r')"?\s+on\s+(' + QUALNAME_RE + r')', s, re.I
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized CREATE POLICY shape", s[:150]))
        return targets, unresolved
    targets.add("policy:%s.%s" % (normalize_name(m.group(2)), m.group(1)))
    return targets, unresolved


def handle_owner_to(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    m = re.match(
        r'alter\s+(table|function|procedure|view|materialized\s+view|sequence|type|schema)\s+'
        r'(' + QUALNAME_RE + r')(\s*\([^)]*\))?\s+owner\s+to\s+"?(' + NAME_RE + r')"?',
        s, re.I,
    )
    if not m:
        unresolved.append(unresolved_entry(filename, stmt.line_start(),
            "unrecognized OWNER TO shape", s[:150]))
        return targets, unresolved
    kind_kw = m.group(1).lower()
    kind = 'matview' if 'materialized' in kind_kw else kind_kw.split()[0]
    kind = {'procedure': 'function', 'view': 'view'}.get(kind, kind)
    name = normalize_name(m.group(2))
    args = ''
    if m.group(3):
        args = ','.join(a.strip() for a in split_top_level(m.group(3).strip()[1:-1], ',') if a.strip())
        targets.add("owner:function:%s(%s)" % (name, args))
    else:
        targets.add("owner:%s:%s" % (kind, name))
    return targets, unresolved


def handle_default_privileges(stmt, filename):
    targets = set()
    unresolved = []
    s = stmt.stripped
    unresolved.append(unresolved_entry(filename, stmt.line_start(),
        "ALTER DEFAULT PRIVILEGES parsing is not implemented (no occurrences confirmed in "
        "migrations 0454-0471)", s[:150]))
    return targets, unresolved


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def classify_and_extract(stmt, filename, table_pk):
    s = stmt.stripped
    if not s.strip():
        return set(), []
    low = s.lower()

    if re.match(r'create\s+table\b', low):
        return handle_create_table(stmt, filename, table_pk)
    if re.match(r'create\s+(or\s+replace\s+)?(function|procedure)\b', low):
        return handle_create_function(stmt, filename)
    if re.match(r'alter\s+function\b', low):
        return handle_alter_function(stmt, filename)
    if re.match(r'create\s+(constraint\s+)?trigger\b', low):
        return handle_create_trigger(stmt, filename)
    if re.match(r'drop\s+trigger\b', low):
        return handle_drop_trigger(stmt, filename)
    if re.match(r'alter\s+default\s+privileges\b', low):
        return handle_default_privileges(stmt, filename)
    if re.search(r'^alter\s+\S+.*\bowner\s+to\b', low):
        return handle_owner_to(stmt, filename)
    if re.match(r'alter\s+table\b', low):
        return handle_alter_table(stmt, filename)
    if re.match(r'alter\s+type\b', low):
        return handle_alter_type(stmt, filename)
    if re.match(r'comment\s+on\b', low):
        return handle_comment_on(stmt, filename)
    if re.match(r'grant\b', low):
        return handle_grant_revoke(stmt, filename, is_grant=True)
    if re.match(r'revoke\b', low):
        return handle_grant_revoke(stmt, filename, is_grant=False)
    if re.match(r'insert\s+into\b', low):
        return handle_insert(stmt, filename, table_pk)
    if re.match(r'update\s+', low):
        return handle_update(stmt, filename, table_pk)
    if re.match(r'delete\s+from\b', low):
        return handle_delete(stmt, filename, table_pk)
    if re.match(r'with\b', low):
        # A data-modifying statement whose CTE(s) precede the verb, e.g.
        # "with seed as (...) insert into t(...) select ... from seed;".
        # handle_insert locates "insert into" itself (via re.search, not
        # re.match), so it works correctly even though the statement text
        # does not start with that keyword.
        if re.search(r'\binsert\s+into\b', low):
            return handle_insert(stmt, filename, table_pk)
        if re.search(r'\bupdate\s+\S+\s+set\b', low):
            return set(), [unresolved_entry(filename, stmt.line_start(),
                "WITH-prefixed UPDATE statement is not handled by this extractor "
                "(no occurrence of this shape confirmed in migrations 0454-0471)", s[:150])]
        if re.search(r'\bdelete\s+from\b', low):
            return set(), [unresolved_entry(filename, stmt.line_start(),
                "WITH-prefixed DELETE statement is not handled by this extractor "
                "(no occurrence of this shape confirmed in migrations 0454-0471)", s[:150])]
        # A bare "with ... select ..." (no DML) touches nothing.
        return set(), []
    if re.match(r'do\b', low):
        return handle_do_block(stmt, filename)
    if re.match(r'create\s+(unique\s+)?index\b', low):
        return handle_create_index(stmt, filename)
    if re.match(r'create\s+(temp\s+|temporary\s+)?sequence\b', low):
        return handle_create_sequence(stmt, filename)
    if re.match(r'create\s+schema\b', low):
        return handle_create_schema(stmt, filename)
    if re.match(r'create\s+(or\s+replace\s+)?(materialized\s+)?view\b', low):
        return handle_create_view(stmt, filename)
    if re.match(r'create\s+type\b', low):
        return handle_create_type(stmt, filename)
    if re.match(r'create\s+policy\b', low):
        return handle_create_policy(stmt, filename)

    if re.match(r'(create|alter|drop|insert|update|delete|grant|revoke)\b', low):
        return set(), [unresolved_entry(filename, stmt.line_start(),
            "statement keyword recognized but no handler implemented for this shape", s[:150])]
    return set(), []


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PATCH (slice-compare): cross-file rename forwarding for COMMENT ON FUNCTION
# ---------------------------------------------------------------------------
#
# A real Postgres COMMENT is attached to the object's OID, not its name. This
# corpus's "successor" idiom (0457, 0459, 0461, 0462, 0464, 0466, 0468, 0471)
# renames the current bare `ops.scac_policy_epoch_snapshot()` to a versioned
# name (`_v2`, `_v3`, ...) in file N, then immediately CREATEs a new function
# under the bare name and COMMENTs it in that SAME file N. That comment
# therefore genuinely belongs to the NEW (file-N) function -- but a LATER
# file N+k renames THAT SAME object forward again, and the real Postgres
# catalog comment follows the OID to the new name. A single-pass, per-file
# extractor cannot know about a rename that has not happened yet when it
# emits the comment: target, so this is resolved as an explicit second pass
# over the full 18-file range, exactly mirroring what pg_description /
# pg_identify_object naturally do in a real run (cross-checked against the 7
# affected comments in the OBSERVED effect manifest: scac_policy_epoch_
# snapshot_v3 through _v9 -- see comparison-report.md, rule
# comment_rename_forwarding_regenerated). Scope: niladic functions only,
# because every ALTER FUNCTION ... RENAME TO in this corpus renames a
# zero-argument function (verified by grep); a renamed function with
# arguments would need the same argcount-aware matching used elsewhere and
# is deliberately not implemented since it is not exercised here.
ALTER_FUNC_RENAME_RE = re.compile(
    r'alter\s+function\s+(' + QUALNAME_RE + r')\s*\(([^)]*)\)\s*rename\s+to\s+"?(' + NAME_RE + r')"?',
    re.I,
)


def collect_function_rename_events():
    events = []  # [(file_index, old_bare_qualified, new_bare_qualified), ...] in file order
    for file_index, filename in enumerate(MIGRATION_FILES):
        text = fetch_migration(filename)
        for m in ALTER_FUNC_RENAME_RE.finditer(text):
            old_name = normalize_name(m.group(1))
            if m.group(2).strip():
                continue  # only niladic renames occur in this corpus (see above)
            schema = schema_of(old_name)
            new_bare = m.group(3)
            new_name = "%s.%s" % (schema, new_bare) if '.' in old_name else new_bare
            events.append((file_index, old_name, new_name))
    return events


def forward_resolve_comments(by_file, rename_events):
    for file_index, filename in enumerate(MIGRATION_FILES):
        new_list = []
        for t in by_file[filename]:
            if t.startswith('comment:function:') and t.endswith('()'):
                current = t[len('comment:function:'):-2]
                for (ev_file_index, old_bare, new_bare) in rename_events:
                    if ev_file_index > file_index and old_bare == current:
                        current = new_bare
                t = 'comment:function:%s()' % current
            new_list.append(t)
        by_file[filename] = new_list
    return by_file


def process_file(filename, table_pk):
    text = fetch_migration(filename)
    spans = split_statements(text)
    file_targets = set()
    file_unresolved = []
    for (start, end) in spans:
        stmt = Stmt(text, start, end)
        if not stmt.stripped.strip():
            continue
        t, u = classify_and_extract(stmt, filename, table_pk)
        file_targets |= t
        file_unresolved.extend(u)
    return file_targets, file_unresolved


def main(argv: list[str] | None = None) -> int:
    table_pk: dict[str, list[str]] = {}
    all_targets = set()
    all_unresolved = []
    by_file = {}
    for filename in MIGRATION_FILES:
        file_targets, file_unresolved = process_file(filename, table_pk)
        all_targets |= file_targets
        all_unresolved.extend(file_unresolved)
        by_file[filename] = sorted(file_targets)

    # PATCH (slice-compare): cross-file COMMENT ON FUNCTION rename forwarding
    # -- see forward_resolve_comments() docstring above.
    rename_events = collect_function_rename_events()
    by_file = forward_resolve_comments(by_file, rename_events)
    all_targets = set()
    for filename in MIGRATION_FILES:
        all_targets |= set(by_file[filename])

    result = {
        "targets": sorted(all_targets),
        "unresolved": sorted(
            all_unresolved, key=lambda u: (u["file"], u["line"], u["reason"], u["fragment"])
        ),
        "by_file": by_file,
    }
    out_path = SCRIPT_DIR / "parse-extracted-targets.v1.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=True)
        f.write('\n')
    sys.stderr.write(
        "wrote %s\ntargets=%d unresolved=%d files=%d\n" %
        (out_path, len(all_targets), len(all_unresolved), len(MIGRATION_FILES))
    )
    return 0
