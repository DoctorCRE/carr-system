#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only acceptance for SECURITY DEFINER object qualification.

0854 pins every public/ops definer's search_path with pg_temp last, which stops
temporary-object substitution. This gate holds the second half of the review
finding that followed it: a definer body must not depend on search_path at all
for the application objects it touches. Every application relation, row type
and helper routine a definer names must be schema-qualified, so a later path
edit, a new same-named object in an earlier schema, or a dropped pg_temp entry
cannot silently change what the routine reads or writes.

What the audit reads. It tokenizes pg_proc.prosrc for every SECURITY DEFINER
routine in public and ops (comments, string literals and quoted identifiers are
skipped) and reports a bare name used as a relation (after FROM, JOIN, UPDATE,
INSERT INTO or MERGE INTO, or before %ROWTYPE/%TYPE) or called as a routine,
when that name is an application relation or routine. Names resolved through
pg_catalog are exempt, and so are a body's own CTE names. A column-qualified
type reference such as `actor.slug%type` is outside what the audit can see.

What else it asserts. The audit refuses a known-unsafe control body, so a
broken tokenizer cannot pass vacuously; the live SCAC frontier catalog still
matches its seal and every historical registry seal still validates (body
qualification must not touch ACLs, owners or configs); and the live registry
frontier is one the SIEP-11 gate supports, which is the predecessor regression
for the v100 entry that gate once dropped.
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]

# Comments, string literals and quoted identifiers are single tokens so the
# audit can skip them; everything else is a word or one punctuation character.
TOKEN = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[a-zA-Z_][\w$]*|[^\s]",
    re.S,
)
CTE_NAME = re.compile(
    r"(?:\bwith(?:\s+recursive)?|,)\s*([a-zA-Z_]\w*)\s*(?:\([^()]*\))?\s+as\s+"
    r"(?:not\s+)?(?:materialized\s+)?\(",
    re.I,
)

Resolver = Callable[[str, str], "str | None"]


def dependency_edits(body: str, resolve: Resolver) -> list[tuple[int, int, str]]:
    """(start, end, qualified) for each bare application reference in body.

    resolve(kind, name) returns the schema a bare name binds to, or None when
    it is not an application object. kind is 'relation' or 'routine'.
    """
    ctes = {m.group(1).lower() for m in CTE_NAME.finditer(body)}
    tokens = [m for m in TOKEN.finditer(body)
              if not m[0].startswith(("--", "/*", "'", '"'))]
    edits = []
    for i, token in enumerate(tokens):
        name = token[0].lower()
        prev = tokens[i - 1][0].lower() if i else ""
        before_prev = tokens[i - 2][0].lower() if i >= 2 else ""
        following = tokens[i + 1][0].lower() if i + 1 < len(tokens) else ""
        after_following = tokens[i + 2][0].lower() if i + 2 < len(tokens) else ""
        if prev == "." or following == ".":
            continue
        schema = None
        relation_context = (
            prev in ("from", "join", "update")
            or (prev == "into" and before_prev in ("insert", "merge"))
            or (following == "%" and after_following in ("rowtype", "type"))
        )
        if relation_context and name not in ctes:
            schema = resolve("relation", name)
        if schema is None and following == "(" and prev not in ("function", "procedure"):
            schema = resolve("routine", name)
        if schema:
            edits.append((token.start(), token.end(), f"{schema}.{token[0]}"))
    return edits


def apply_edits(body: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, reverse=True):
        body = body[:start] + replacement + body[end:]
    return body


class Catalog:
    """Which schemas hold each application relation and routine name."""

    def __init__(self, cur):
        self.relations: dict[str, set[str]] = defaultdict(set)
        self.routines: dict[str, set[str]] = defaultdict(set)
        for name, schema in cur.execute(
            """select c.relname,n.nspname from pg_class c
                 join pg_namespace n on n.oid=c.relnamespace
                where n.nspname in ('pg_catalog','public','ops')
                  and c.relkind in ('r','p','v','m','f','c')"""
        ).fetchall():
            self.relations[name].add(schema)
        for name, schema in cur.execute(
            """select distinct p.proname,n.nspname from pg_proc p
                 join pg_namespace n on n.oid=p.pronamespace
                where n.nspname in ('pg_catalog','public','ops')"""
        ).fetchall():
            self.routines[name].add(schema)

    def any_application(self, kind: str, name: str) -> str | None:
        """Audit resolver: any bare name that is an application object."""
        schemas = (self.relations if kind == "relation" else self.routines).get(name, set())
        if "pg_catalog" in schemas:
            return None
        app = sorted(schemas & {"public", "ops"})
        return app[0] if app else None

    def path_resolver(self, search_path: list[str]) -> Resolver:
        """Generator resolver: the schema the routine's own path binds to.

        pg_catalog is searched first when the path does not name it.
        """
        order = ([] if "pg_catalog" in search_path else ["pg_catalog"]) + [
            s for s in search_path if s != "pg_temp"
        ]

        def resolve(kind: str, name: str) -> str | None:
            schemas = (self.relations if kind == "relation" else self.routines).get(name, set())
            for schema in order:
                if schema in schemas:
                    return None if schema == "pg_catalog" else schema
            return None

        return resolve


def definer_rows(cur):
    return cur.execute(
        """select p.oid,p.oid::regprocedure::text,p.prosrc,p.proconfig
             from pg_proc p join pg_namespace n on n.oid=p.pronamespace
            where p.prosecdef and n.nspname in ('public','ops')
            order by 2"""
    ).fetchall()


def unqualified_definers(cur) -> list[tuple[str, list[str]]]:
    catalog = Catalog(cur)
    failures = []
    for _oid, signature, body, _config in definer_rows(cur):
        edits = dependency_edits(body, catalog.any_application)
        if edits:
            failures.append((signature, sorted({value for _s, _e, value in edits})))
    return failures


def require_loopback(dsn: str) -> None:
    from psycopg.conninfo import conninfo_to_dict

    # Loopback only, like every db-gate: ops/ci.sh refuses a non-loopback DSN
    # before any gate runs, and this repeats it for direct invocation. Hosted
    # CI's throwaway service carries a password, so a password is allowed.
    info = conninfo_to_dict(dsn)
    if info.get("host") not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("requires a disposable loopback database")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        print("definer-hardening-local-pg-gate: FAIL — DATABASE_URL or CARR_LOCAL_PG_DSN is required",
              file=sys.stderr)
        return 1
    import psycopg

    try:
        require_loopback(dsn)
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            catalog = Catalog(cur)
            if not dependency_edits("select id into v from actor where slug=$1", catalog.any_application):
                raise RuntimeError("qualification audit did not flag its unsafe control")
            if dependency_edits("select id into v from public.actor where slug=$1", catalog.any_application):
                raise RuntimeError("qualification audit flagged a qualified control")
            definers = definer_rows(cur)
            failures = unqualified_definers(cur)
            if failures:
                raise RuntimeError(
                    f"{len(failures)} SECURITY DEFINER routine(s) depend on search_path for "
                    f"application objects: {failures[:20]!r}"
                )
            live = cur.execute(
                """select registry_version from ops.scac_mutation_registry_version
                    order by regexp_replace(registry_version,'^.*[.]v','','')::integer desc limit 1"""
            ).fetchall()[0][0]
            if live not in {'scac-mutation-registry.v118'}:
                raise RuntimeError(f"unsupported live successor {live!r}")
            ordinal = int(live.rsplit(".v", 1)[1])
            if cur.execute(f"select ops.scac_mutation_catalog_v{ordinal}_current()").fetchall()[0][0] is not True:
                raise RuntimeError(f"live catalog no longer matches the {live} seal")
            invalid = [
                version for (version,) in cur.execute(
                    "select registry_version from ops.scac_mutation_registry_version order by 1"
                ).fetchall()
                if cur.execute("select ops.scac_mutation_registry_seal_valid(%s)", (version,)).fetchall()[0][0]
                is not True
            ]
            if invalid:
                raise RuntimeError(f"historical registry seal(s) no longer validate: {invalid!r}")
            conn.rollback()
    except Exception as exc:  # noqa: BLE001 - a gate reports every failure the same way
        print(f"definer-hardening-local-pg-gate: FAIL — {exc}", file=sys.stderr)
        return 1
    print(
        f"db-gate-proof: definer-hardening audited {len(definers)} SECURITY DEFINER routines "
        f"(0 unqualified), refused its unsafe control, live {live} supported and its catalog seal current"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
