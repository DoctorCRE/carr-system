#!/usr/bin/env python3
"""Read-only usage evidence for application tables. Never a retirement decision."""
from collections import Counter, defaultdict
import re
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


STATS_RESET_CAVEAT = (
    "Mutation counters cover only the interval since stats_reset and can lag. "
    "Per-table resets can be newer and are not timestamped here. A zero counter "
    "is never proof of disuse. reltuples is an estimate (-1 means unknown), "
    "not an exact row count. empty-and-unreferenced means estimated empty with "
    "no references found in this scan, never permission to remove a table."
)


def build_report(tables, *, references, unresolved, backup_tables):
    rows = []
    for table in tables:
        name = f"{table['schema']}.{table['name']}"
        refs = references.get(name, [])
        mutations = [table.get(key) for key in ("inserts", "updates", "deletes")]
        if any(value is not None and value > 0 for value in mutations):
            classification, reason = "active", "Observed positive mutation counter."
        elif (table["estimated_rows"] == 0 and not refs and not unresolved
              and all(value == 0 for value in mutations)):
            classification = "empty-and-unreferenced"
            reason = "Zero row estimate and counters; no scanned references or unresolved SQL."
        else:
            classification = "quiet"
            reason = "No positive mutation counter; rows, references, or uncertainty remain."
        rows.append({**table, "table": name, "references": refs,
                     "reference_status": "unresolved" if unresolved else "static_scan_only",
                     "in_backup_census": name in backup_tables,
                     "classification": classification, "classification_reason": reason})
    counts = Counter(row["classification"] for row in rows)
    return {
        "schema": "carr-table-usage-census/v1",
        "stats_reset_caveat": STATS_RESET_CAVEAT,
        "counts": {key: counts[key] for key in ("active", "quiet", "empty-and-unreferenced")},
        "tables": sorted(rows, key=lambda row: row["table"]),
        "unresolved": unresolved,
        "top_25_by_size": [{key: row[key] for key in ("table", "total_bytes", "classification")}
                           for row in sorted(rows, key=lambda row: (-(row["total_bytes"] or 0), row["table"]))[:25]],
    }


SOURCE_ROOTS = ("mcp-server/src", "ops", "tools", "lib", "pipelines")
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".tsx", ".sql", ".sh", ".applescript"}
SENSITIVE_PATH = re.compile(r"(?:^|[/_.-])(?:env|credentials?|tokens?|keys?|keychains?|dumps?|secrets?)(?:$|[/_.-])", re.I)
SYMBOL = re.compile(r"(?:\b(?:async\s+)?def\s+|\bfunction\s+|\b(?:const|let)\s+)([A-Za-z_$][\w$]*)")
DYNAMIC_SQL = re.compile(
    r"\bEXECUTE\b|\b(?:sql\.)?(?:Identifier|SQL)\s*\(|"
    r"(?:from|join|into|update|table)\s+[\"']?(?:\{|\$|%[sI])|"
    r"\b(?:query|execute|executemany)\s*\(\s*[A-Za-z_]\w*\s*[,)]|"
    r"\bsql`|\b(?:sql|query)\s*\+", re.I)


def scan_references(root, tables, paths, inventory, functions):
    """Lexical evidence, with module verb candidates explicitly distinguished."""
    references, unresolved = defaultdict(list), []
    names = defaultdict(list)
    for table in tables:
        names[table["name"]].append(f"{table['schema']}.{table['name']}")
    verbs = defaultdict(list)
    for entry in inventory:
        source = entry.get("source_locator")
        operation = entry.get("operation")
        if source and operation:
            verbs[source].append(operation)
        written_tables = entry.get("tables_written", [])
        if not isinstance(written_tables, list):
            unresolved.append({"kind": "inventory_table_unresolved", "operation": operation})
            written_tables = []
        for written in written_tables:
            if not isinstance(written, str):
                unresolved.append({"kind": "inventory_table_unresolved", "operation": operation})
                continue
            targets = [written] if "." in written else names.get(written, [])
            for target in targets:
                references[target].append({"kind": "tables_written", "operation": operation, "source": source})

    candidates_emitted = set()

    def scan(source, text, signature=None):
        symbol = None
        for number, line in enumerate(text.splitlines(), 1):
            declaration = SYMBOL.search(line)
            if declaration:
                symbol = declaration[1]
            location = {"source": source, "line": number}
            if signature:
                location["function"] = signature
            if DYNAMIC_SQL.search(line):
                unresolved.append({"kind": "dynamic_sql", **location})
            lexical_line = re.sub(r"\$[A-Za-z_0-9]*\$", " ", line)
            for token in sorted(set(re.findall(r"[A-Za-z_][A-Za-z_0-9$]*", lexical_line)) & names.keys()):
                for target in names[token]:
                    evidence = {"kind": "function_static" if signature else "source_static", **location}
                    if symbol and not signature:
                        evidence["symbol"] = symbol
                        evidence["symbol_evidence"] = "nearest_declaration_candidate"
                    if verbs[source] and (target, source) not in candidates_emitted:
                        evidence["candidate_verbs"] = sorted(verbs[source])
                        evidence["verb_evidence"] = "same_module_candidate_not_call_graph"
                        candidates_emitted.add((target, source))
                    references[target].append(evidence)

    for source in sorted(paths):
        path = root / source
        if SENSITIVE_PATH.search(source):
            unresolved.append({"kind": "excluded_sensitive_path", "source": source})
        elif path.suffix in SOURCE_SUFFIXES:
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                unresolved.append({"kind": "excluded_symlink", "source": source})
            else:
                scan(source, path.read_text(encoding="utf-8"))
    for function in functions:
        scan("pg_proc", function["definition"], function["signature"])
    return dict(references), unresolved


def render_summary(report):
    counts = ", ".join(f"{name}={count}" for name, count in report["counts"].items())
    lines = [f"Table usage census: {len(report['tables'])} tables; {counts}",
             "Database stats_reset: " + ", ".join(sorted({str(row.get("stats_reset") or "unknown") for row in report["tables"]})),
             report["stats_reset_caveat"],
             f"Unresolved evidence: {len(report['unresolved'])}. See JSON for locations.",
             "Top 25 tables by total size (bytes, including indexes and TOAST):"]
    lines.extend(f"{row['total_bytes']}\t{row['table']}\t{row['classification']}"
                 for row in report["top_25_by_size"])
    return "\n".join(lines)


def collect_catalog(conn):
    """Metadata only. Reuse the actual backup selection without exporting a snapshot."""
    from psycopg.rows import dict_row

    spec = importlib.util.spec_from_file_location("census_backup_guard", ROOT / "bin/backup-guard.py")
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("set transaction isolation level repeatable read read only")
        cur.execute("set local statement_timeout = '60s'")
        cur.execute("set local lock_timeout = '2s'")
        tables = cur.execute("""
            select n.nspname as schema, c.relname as name, c.relkind as kind,
                   c.reltuples::double precision as estimated_rows,
                   s.n_tup_ins as inserts, s.n_tup_upd as updates, s.n_tup_del as deletes,
                   d.stats_reset, s.last_vacuum, s.last_autovacuum,
                   s.last_analyze, s.last_autoanalyze,
                   case when c.relkind='f' then null else pg_total_relation_size(c.oid) end as total_bytes
            from pg_class c join pg_namespace n on n.oid=c.relnamespace
            left join pg_stat_user_tables s on s.relid=c.oid
            left join pg_stat_database d on d.datname=current_database()
            where n.nspname in ('public','ops') and c.relkind in ('r','p','f')
            order by n.nspname,c.relname
        """).fetchall()
        functions = cur.execute("""
            select n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')' as signature,
                   pg_get_functiondef(p.oid) as definition
            from pg_proc p join pg_namespace n on n.oid=p.pronamespace
            where n.nspname in ('public','ops') and p.prokind in ('f','p')
              and not exists (select 1 from pg_depend dep
                              where dep.classid='pg_proc'::regclass and dep.objid=p.oid
                                and dep.deptype='e')
            order by p.oid
        """).fetchall()
        backup = cur.execute(guard.CENSUS, (None, None, None, None, False)).fetchone()
        backup = next(iter(backup.values()))
        readonly = cur.execute("select current_setting('transaction_read_only') as value").fetchone()["value"]
    return {"tables": tables, "functions": functions,
            "backup_tables": [f"{row['schema']}.{row['name']}" for row in backup["tables"]],
            "transaction_read_only": readonly,
            "backup_census_sha256": hashlib.sha256(guard.CENSUS.encode()).hexdigest()}


def source_inventory():
    script = (
        'import { mcpInventory } from "./ops/scac-mutation-inventory.mjs";'
        'import { TOOLS } from "./mcp-server/src/tools.js";'
        'console.log(JSON.stringify(mcpInventory(TOOLS)));'
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("SCAC source inventory failed; refusing an incomplete census")
    return json.loads(result.stdout)


def write_report(report, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(report, output, indent=2, sort_keys=True, default=str)
            output.write("\n")
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    import psycopg
    sys.path.insert(0, str(ROOT))
    from lib.credential_file import credential

    dsn = credential("DATABASE_URL_READER")
    if not dsn:
        print("table-usage-census: DATABASE_URL_READER required", file=sys.stderr)
        return 2
    with psycopg.connect(dsn, connect_timeout=15) as conn:
        snapshot = collect_catalog(conn)
    inventory = source_inventory()
    paths = subprocess.run(["git", "ls-files", "-z", "--", *SOURCE_ROOTS], cwd=ROOT,
                           capture_output=True, check=True).stdout.decode().split("\0")
    references, unresolved = scan_references(ROOT, snapshot["tables"],
                                            [path for path in paths if path], inventory, snapshot["functions"])
    report = build_report(snapshot["tables"], references=references, unresolved=unresolved,
                          backup_tables=set(snapshot["backup_tables"]))
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["provenance"] = {
        "source_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                      text=True, check=True).stdout.strip(),
        "source_roots": SOURCE_ROOTS,
        "source_files_considered": len([path for path in paths if path]),
        "inventory_entries": len(inventory),
        "inventory_entries_with_tables_written": sum("tables_written" in item for item in inventory),
        "transaction_read_only": snapshot["transaction_read_only"],
        "backup_census_source": "bin/backup-guard.py:CENSUS",
        "backup_census_sha256": snapshot["backup_census_sha256"],
    }
    report["reference_caveat"] = (
        "Lexical matches can include comments, tests and ambiguous names. Symbols are nearest-declaration "
        "candidates; candidate_verbs share a module and do not prove a call graph. Dynamic SQL and "
        "excluded sensitive paths are globally unresolved, so every table remains potentially referenced. "
        "Static absence never proves runtime absence."
    )
    report["backup_caveat"] = "Membership in the current backup census scope only; not proof of a successful backup."
    report["size_caveat"] = "Per-relation bytes include indexes and TOAST. Partition parents do not sum children. Foreign sizes are unknown."
    path = ROOT / "out/table-usage-census.json"
    write_report(report, path)
    print(render_summary(report))
    print(f"Artifact: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"table-usage-census failed ({type(exc).__name__}); no complete census produced", file=sys.stderr)
        raise SystemExit(1) from None
