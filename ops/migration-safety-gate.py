#!/usr/bin/env python3
"""migration-safety-gate.py — added migrations declare their risk; the snapshot keeps up.

TWO CHECKS, both static, both in ops/ci.sh's gates class.

1. HEADER CONTRACT for every migration this change ADDS (same "added" rule as
   ops/migration-order-gate.py: in HEAD, in neither the merge base nor the base).
   The header is the run of comment lines before the first statement.

     -- rollback: <how to undo it>                     always required
     -- rollback: forward-only — <why it cannot be undone>
     -- expand-contract: <expand|migrate|contract> — <why this phase is safe now>
                                                       required when a statement is destructive
     -- lock-review: <why the lock is acceptable>      required when a statement takes a long lock

   DESTRUCTIVE: drop table/schema/column, truncate, delete without where,
   table/column rename, column type change (narrowing cannot be told apart
   from widening without the catalog, and both rewrite under an exclusive
   lock), a NOT NULL column added without a default, and SET NOT NULL — the
   last two on tables this migration did not create.
   LONG LOCK, on tables this migration did not create: a non-concurrent index,
   a validated foreign key / check / unique / primary key constraint, a
   volatile or serial default (table rewrite), vacuum full, cluster, and an
   explicit access-exclusive lock. tools/migrate.py runs every migration in a
   transaction, so CONCURRENTLY is not available; the review line is the move.
   Drops of views, functions, triggers, policies and indexes are not data loss
   and are not flagged.

   This is a lexical check: comments and quoted strings are ignored, DO-block
   and function bodies are read (DDL inside them still runs). It errs toward
   asking for a marker; the marker is one line.

2. SNAPSHOT CURRENTNESS. db/schema.sql's applied-migration ledger must name
   every migration in the tree with the checksum tools/migrate.py records, and
   nothing else. A migration that is not in the ledger means the reference
   schema is behind the migrations that define it. Regenerate with
   ops/migration-shadow.py (which also proves the snapshot is exactly what the
   base snapshot plus the pending migrations produce).

Exit 0 clean · 1 a finding · 2 an input could not be read, which is not a pass.

  ops/migration-safety-gate.py                 # base = origin/$GITHUB_BASE_REF or origin/main
  ops/migration-safety-gate.py --base <ref> [--head <ref>]
"""
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Mapping

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from migration_number_contract import SLOT_RE  # noqa: E402

MIGRATIONS_DIR = "migrations"
SNAPSHOT = "db/schema.sql"
REGENERATE = ("regenerate db/schema.sql with ops/migration-shadow.py --write "
              "(needs PostgreSQL 18), or commit the schema.sql artifact the CI "
              "migration job uploads when it reports the snapshot stale")


@dataclass(frozen=True)
class Finding:
    kind: str  # rollback-missing | destructive | long-lock
    detail: str


# ── lexing ─────────────────────────────────────────────────────────────────

DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def code_text(sql: str) -> str:
    """The SQL with comments and quoted literals blanked. Dollar-quoted bodies
    are kept: a DO block or function body runs the DDL it contains."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif c == "'":
            escaped = i > 0 and sql[i - 1] in "eE"
            j = i + 1
            while j < n:
                if escaped and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append("''")
            i = j + 1
        elif c == "$" and (m := DOLLAR_TAG.match(sql, i)):
            out.append(" ")  # the tag itself; the body is read as code
            i = m.end()
        else:
            out.append(c)
            i += 1
    return "".join(out)


def header_lines(sql: str) -> list[str]:
    lines: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("--"):
            break
        lines.append(stripped)
    return lines


def statements(sql: str) -> list[str]:
    text = re.sub(r"\s+", " ", code_text(sql).lower())
    return [s.strip() for s in text.split(";") if s.strip()]


# ── header markers ─────────────────────────────────────────────────────────

ROLLBACK = re.compile(r"^--\s*rollback:\s*(.*)$", re.I)
FORWARD_ONLY = re.compile(r"^forward[- ]only\b(.*)$", re.I)
EXPAND_CONTRACT = re.compile(
    r"^--\s*expand-contract:\s*(expand|migrate|contract)\b\s*[—–:,(-]?\s*(\S.{5,})$", re.I)
LOCK_REVIEW = re.compile(r"^--\s*lock-review:\s*(\S.{9,})$", re.I)


def rollback_ok(header: list[str]) -> bool:
    for line in header:
        m = ROLLBACK.match(line)
        if not m:
            continue
        note = m.group(1).strip()
        fo = FORWARD_ONLY.match(note)
        if fo:
            reason = fo.group(1).strip().lstrip("—–:,(-").strip()
            return len(reason) >= 6
        return len(note) >= 3
    return False


# ── statement classification ───────────────────────────────────────────────

NAME = r'((?:"[^"]+"|[a-z_][a-z0-9_$]*)(?:\.(?:"[^"]+"|[a-z_][a-z0-9_$]*))?)'
CREATE_TABLE = re.compile(
    r"^create (?:(?:global |local )?(?:temp|temporary|unlogged) )?table (?!if not exists )" + NAME)
ALTER_TABLE = re.compile(r"\balter table (?:if exists )?(?:only )?" + NAME + r"(.*)$")
CREATE_INDEX = re.compile(
    r"\bcreate (?:unique )?index (?!concurrently)(?:if not exists )?(?:\S+ )?on (?:only )?" + NAME)
CLAUSE_SPLIT = re.compile(
    r",\s*(?=(?:add|drop|alter|rename|validate|set|owner|enable|disable|inherit|no)\b)")
DROP_NON_COLUMN = re.compile(
    r"^drop (?:constraint|default|not null|identity|expression)\b")
VOLATILE_DEFAULT = re.compile(
    r"\bdefault (?:public\.)?(?:gen_random_uuid|uuid_generate_v[14]|random|clock_timestamp|"
    r"timeofday|nextval)\s*\(")


def bare(name: str) -> str:
    name = name.replace('"', "")
    return name.split(".", 1)[1] if name.startswith("public.") else name


def created_tables(stmts: list[str]) -> set[str]:
    return {bare(m.group(1)) for s in stmts for m in CREATE_TABLE.finditer(s)}


def outer_where(tail: str) -> bool:
    depth = 0
    for token in re.findall(r'"[^"]*"|[a-z_][a-z0-9_$]*|[()]', tail):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0 and token == "returning":
            return False
        elif depth == 0 and token == "where":
            return True
    return False


def classify(stmt: str, created: set[str]) -> list[tuple[str, str]]:
    """[(kind, reason)] for one normalised statement."""
    hits: list[tuple[str, str]] = []
    if re.search(r"\bdrop table\b", stmt):
        hits.append(("destructive", "drop table"))
    if re.search(r"\bdrop schema\b", stmt):
        hits.append(("destructive", "drop schema"))
    if (re.search(r"(?<!before )(?<!after )(?<!or )\btruncate (?:table )?(?:only )?[a-z_\"]", stmt)
            and not re.search(r"\b(?:grant|revoke)\b", stmt)):
        hits.append(("destructive", "truncate"))
    m = re.search(r"\bdelete from (?:only )?\S+(.*)$", stmt)
    if m and not outer_where(m.group(1)):
        hits.append(("destructive", "delete without where"))
    if re.search(r"\bvacuum (?:\([^)]*full[^)]*\)|full)\b", stmt):
        hits.append(("long-lock", "vacuum full"))
    if re.search(r"(?:^|\bbegin |; )cluster\b", stmt) or stmt.startswith("cluster "):
        hits.append(("long-lock", "cluster"))
    lock = re.search(r"\block (?:table )?(?:only )?\S+(.*)$", stmt)
    if lock and not stmt.startswith("select") and (
            " in " not in lock.group(1) or "access exclusive" in lock.group(1)):
        hits.append(("long-lock", "access exclusive lock"))

    idx = CREATE_INDEX.search(stmt)
    if idx and bare(idx.group(1)) not in created:
        hits.append(("long-lock", f"non-concurrent index on {bare(idx.group(1))}"))

    alter = ALTER_TABLE.search(stmt)
    if not alter:
        return hits
    table, rest = bare(alter.group(1)), alter.group(2).strip()
    existing = table not in created
    if re.match(r"^rename (?:column )?(?!constraint\b)\S+ to\b", rest) or rest.startswith("rename to"):
        hits.append(("destructive", f"rename on {table}"))
    for clause in CLAUSE_SPLIT.split(rest):
        clause = clause.strip()
        if clause.startswith("drop ") and not DROP_NON_COLUMN.match(clause):
            hits.append(("destructive", f"drop column on {table}"))
        if re.match(r"^alter (?:column )?\S+ (?:set data )?type\b", clause) and existing:
            hits.append(("destructive", f"column type change on {table}"))
        if re.match(r"^alter (?:column )?\S+ set not null\b", clause) and existing:
            hits.append(("destructive", f"set not null on {table}"))
        if clause.startswith("add ") and not clause.startswith("add constraint") and existing:
            col = re.sub(r"^add (?:column )?(?:if not exists )?", "", clause)
            if (re.search(r"\bnot null\b", col) and not re.search(r"\bdefault\b", col)
                    and not re.search(r"\bgenerated\b", col)):
                hits.append(("destructive", f"not null column without default on {table}"))
            if (re.search(r"\b(?:references|check|unique|primary key)\b", col)
                    and "not valid" not in col):
                hits.append(("long-lock", f"validated column constraint on {table}"))
            if VOLATILE_DEFAULT.search(col) or re.search(r"^\S+ (?:small|big)?serial\b", col):
                hits.append(("long-lock", f"table rewrite (volatile default) on {table}"))
        if (re.match(r"^add (?:constraint \S+ )?(?:foreign key|check|unique|primary key)\b", clause)
                and existing and "not valid" not in clause and "using index" not in clause):
            hits.append(("long-lock", f"validated constraint on {table}"))
    return hits


def findings(sql: str) -> list[Finding]:
    header = header_lines(sql)
    out: list[Finding] = []
    if not rollback_ok(header):
        out.append(Finding("rollback-missing",
                           "header has no '-- rollback: <how>' or "
                           "'-- rollback: forward-only — <reason>' line"))
    stmts = statements(sql)
    created: set[str] = set()
    marked = {
        "destructive": any(EXPAND_CONTRACT.match(line) for line in header),
        "long-lock": any(LOCK_REVIEW.match(line) for line in header),
    }
    seen: set[tuple[str, str]] = set()
    for stmt in stmts:
        created.update(created_tables([stmt]))
        for kind, reason in classify(stmt, created):
            if marked[kind] or (kind, reason) in seen:
                continue
            seen.add((kind, reason))
            out.append(Finding(kind, f"{reason}: {stmt[:140]}"))
    return out


# ── snapshot ledger ────────────────────────────────────────────────────────

LEDGER_HEAD = re.compile(r"^COPY public\.schema_migrations \(([^)]*)\) FROM stdin;$", re.M)


def runner_sha256(text: str) -> str:
    """The checksum tools/migrate.py records: sha256 of Path.read_text()."""
    return hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()


def ledger_rows(snapshot: str) -> dict[str, str] | None:
    m = LEDGER_HEAD.search(snapshot)
    if not m:
        return None
    columns = [c.strip() for c in m.group(1).split(",")]
    fi, si = columns.index("filename"), columns.index("sha256")
    rows: dict[str, str] = {}
    for line in snapshot[m.end():].lstrip("\n").splitlines():
        if line == "\\.":
            return rows
        fields = line.split("\t")
        rows[fields[fi]] = fields[si]
    return None


def ledger_problems(files: Mapping[str, str], snapshot: str) -> list[str]:
    rows = ledger_rows(snapshot)
    if rows is None:
        return [f"{SNAPSHOT} has no complete schema_migrations ledger section"]
    problems: list[str] = []
    for name in sorted(files):
        if name not in rows:
            problems.append(f"{name} is not in the {SNAPSHOT} ledger")
        elif rows[name] != runner_sha256(files[name]):
            problems.append(f"{name} checksum differs from the {SNAPSHOT} ledger")
    for name in sorted(set(rows) - set(files)):
        problems.append(f"{name} is in the {SNAPSHOT} ledger but has no file in {MIGRATIONS_DIR}/")
    return problems


# ── git plumbing ───────────────────────────────────────────────────────────

def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout


def migration_names(repo: pathlib.Path, ref: str) -> set[str]:
    out = git(repo, "ls-tree", "--name-only", f"{ref}:{MIGRATIONS_DIR}")
    return {name for name in out.splitlines() if SLOT_RE.match(name)}


def read_blobs(repo: pathlib.Path, ref: str, paths: list[str]) -> dict[str, str]:
    request = "".join(f"{ref}:{p}\n" for p in paths).encode()
    raw = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, input=request,
                         capture_output=True, check=True).stdout
    out: dict[str, str] = {}
    pos = 0
    for path in paths:
        end = raw.index(b"\n", pos)
        header = raw[pos:end].split()
        if len(header) < 3 or header[1] != b"blob":
            raise FileNotFoundError(f"{ref}:{path}")
        size = int(header[2])
        out[path] = raw[end + 1:end + 1 + size].decode()
        pos = end + 1 + size + 1
    return out


def default_base(environ: Mapping[str, str]) -> str:
    ref = (environ.get("GITHUB_BASE_REF") or "").strip()
    return f"origin/{ref}" if ref else "origin/main"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default=None)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", default=str(REPO))
    args = parser.parse_args(argv)
    repo = pathlib.Path(args.repo)
    base = args.base or default_base(os.environ)

    try:
        base_names = migration_names(repo, base)
        head_names = migration_names(repo, args.head)
        fork = git(repo, "merge-base", base, args.head).strip()
        fork_names = migration_names(repo, fork)
        texts = read_blobs(repo, args.head,
                           [f"{MIGRATIONS_DIR}/{n}" for n in sorted(head_names)] + [SNAPSHOT])
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as exc:
        detail = getattr(exc, "stderr", None) or exc
        print(f"migration-safety-gate: cannot read migrations or {SNAPSHOT} at "
              f"{args.head!r} / base {base!r}: {str(detail).strip()}", file=sys.stderr)
        print("  This is not a pass. Fetch the base (git fetch origin) and re-run.",
              file=sys.stderr)
        return 2

    added = sorted(head_names - fork_names - base_names)
    by_file = {name: findings(texts[f"{MIGRATIONS_DIR}/{name}"]) for name in added}
    stale = ledger_problems({n: texts[f"{MIGRATIONS_DIR}/{n}"] for n in head_names},
                            texts[SNAPSHOT])

    failed = False
    for name, found in by_file.items():
        if not found:
            continue
        failed = True
        print(f"migration-safety-gate: {MIGRATIONS_DIR}/{name}", file=sys.stderr)
        for f in found:
            print(f"  [{f.kind}] {f.detail}", file=sys.stderr)
        kinds = {f.kind for f in found}
        if "destructive" in kinds:
            print("  Declare it: '-- expand-contract: <expand|migrate|contract> — <why this phase "
                  "is safe now>' in the header. A contract step ships only after every reader "
                  "and writer of the old shape is gone.", file=sys.stderr)
        if "long-lock" in kinds:
            print("  Declare it: '-- lock-review: <table size and why the lock is acceptable>' "
                  "in the header.", file=sys.stderr)
        if "rollback-missing" in kinds:
            print("  Add '-- rollback: <how to undo it>' or '-- rollback: forward-only — <why>' "
                  "to the header.", file=sys.stderr)
    if stale:
        failed = True
        print(f"migration-safety-gate: {SNAPSHOT} is STALE against {MIGRATIONS_DIR}/:",
              file=sys.stderr)
        for line in stale[:20]:
            print(f"  {line}", file=sys.stderr)
        if len(stale) > 20:
            print(f"  ... and {len(stale) - 20} more", file=sys.stderr)
        print(f"  Fix: {REGENERATE}.", file=sys.stderr)
    if failed:
        return 1
    print(f"migration-safety-gate: OK — {len(added)} added migration(s) declare their risk; "
          f"{SNAPSHOT} ledger covers all {len(head_names)} migrations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
