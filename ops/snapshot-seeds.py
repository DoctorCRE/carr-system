#!/usr/bin/env python3
"""Render only repository-reviewed seed selectors at their applied ledger state."""
import json
from pathlib import Path
import re
import subprocess
import sys


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def declarations(repo):
    config = json.loads((Path(repo) / "ops/config/snapshot-seed-coverage.json").read_text())
    reviewed = set().union(*(config.get(key, {}) for key in
                            ("carried", "carried_after_apply", "carried_subset")))
    result = config["renders"]
    for seed in result:
        table = seed["table"]
        normalized = table.removeprefix("public.")
        if normalized not in reviewed or not re.fullmatch(r"[a-z_][a-z_0-9]*\.[a-z_][a-z_0-9]*", table):
            raise ValueError(f"snapshot seed is not classified for carrying: {table}")
        if not seed["columns"]:
            raise ValueError(f"snapshot seed has no reviewed columns: {table}")
        for column in seed["columns"]:
            if not re.fullmatch(r"[a-z_][a-z_0-9]*", column):
                raise ValueError(f"invalid seed column: {table}")
    return result


def query(psql, statement):
    result = subprocess.run([*psql, "-XAtq", "-v", "ON_ERROR_STOP=1", "-c", statement],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def selected_rows(repo, psql):
    applied = set(query(psql, "select coalesce(jsonb_agg(filename),'[]'::jsonb) from public.schema_migrations"))
    selected = []
    for seed in declarations(repo):
        if seed["migration"] not in applied:
            continue
        table = seed["table"]
        columns = ",".join('"' + column + '"' for column in seed["columns"])
        rows = query(psql, f"select coalesce(jsonb_agg(to_jsonb(s) order by {seed['order_by']}),'[]'::jsonb) "
                          f"from (select {columns} from {table} where {seed['where']}) s")
        if len(rows) < seed.get("min_rows", 1) or ("exact_rows" in seed and len(rows) != seed["exact_rows"]):
            raise ValueError(f"db/schema.sql seed {table} from {seed['migration']} has an unexpected row count")
        selected.append((seed, rows))
    return selected


def render(selected):
    for seed, rows in selected:
        table = seed["table"]
        yield f"-- CARR REVIEWED SEED {table} ({seed['migration']})"
        for row in rows:
            columns = ",".join('"' + name.replace('"', '""') + '"' for name in sorted(row))
            payload = literal(json.dumps(row, sort_keys=True, ensure_ascii=False))
            yield (f"insert into {table} ({columns}) select {columns} from "
                   f"jsonb_populate_record(null::{table}, {payload}::jsonb);")


def compare(repo, source_psql, rebuilt_psql):
    source = selected_rows(repo, source_psql)
    rebuilt = selected_rows(repo, rebuilt_psql)
    if source != rebuilt:
        for seed, rows in source:
            if (seed, rows) not in rebuilt:
                raise ValueError(f"db/schema.sql rebuild lost seed rows for {seed['table']} from {seed['migration']}")
        raise ValueError("db/schema.sql rebuild changed the reviewed seed ledger")


if __name__ == "__main__":
    try:
        print("\n".join(render(selected_rows(Path(sys.argv[1]), [sys.argv[2]]))))
    except (ValueError, subprocess.CalledProcessError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "could not read reviewed seed rows"
        sys.exit(f"schema-snapshot: {message}; nothing written")
