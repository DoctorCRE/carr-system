"""Cell-level reconciliation: staged exports vs the live vault files (A8 fidelity).

Fidelity = extracted-cell equality on the columns consumers read, keyed by
record ID — never file bytes. Output is the reconciliation report Joe signs
at freeze; before freeze it is the drift-finder for the rehearsal loop.

Usage: .venv/bin/python tools/reconcile.py [--vault PATH] [--staged PATH]
"""

import argparse
import os
import json
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

VAULT_DEFAULT = str(Path.home() / "Library" / "CloudStorage"
                    / "GoogleDrive-joe.bookout.carr.us@gmail.com" / "My Drive" / "CARR AI")
REPO = Path(__file__).resolve().parent.parent


def sheet_dict(path, sheet, key_col):
    """Extract a sheet as {key -> row}, PLUS the counts that keying alone hides.

    Keyed comparison is the right fidelity test, but it is blind by construction:
    a row whose key cell is empty has no key, so it silently leaves the comparison.
    That blindness shipped a false zero on 2026-07-31 — 12 ref-less clients were
    about to be written into the roster and the reconciler reported no diffs,
    because it never saw them. So the extractor now also returns the TOTAL data-row
    count and the blank-key rows themselves, and the caller reports both loudly.
    Nothing is skipped quietly again.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header = [str(h).strip() if h else None for h in rows[0]]
    ki = header.index(key_col)
    out, blank, total = {}, [], 0
    for r in rows[1:]:
        if not any(v not in (None, "") for v in r):
            continue                                   # truly empty spacer row
        total += 1
        if r[ki] is None or not str(r[ki]).strip():
            blank.append({header[i]: r[i] for i in range(len(header)) if header[i]})
            continue
        out[str(r[ki]).strip()] = {header[i]: r[i] for i in range(len(header)) if header[i]}
    return {"rows": out, "total": total, "blank": blank}


def as_extract(mapping):
    """Wrap an already-keyed mapping (the deals JSON) in the extract shape."""
    return {"rows": mapping, "total": len(mapping), "blank": []}


def md_table(path):
    """Parse the pipe tables in a markdown file into {C-ID: row}. Skips repeat headers."""
    rows, header = {}, None
    for line in Path(path).read_text().splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        if header is None:
            header = cells
            continue
        if cells[:len(header)] == header:
            continue
        d = dict(zip(header, cells + [""] * (len(header) - len(cells))))
        cid = (d.get("C-ID") or "").strip()
        if cid:
            rows.setdefault(cid, d)
    return rows


def active_book_review(curated_path, derived_path, db_url):
    """clients-active.md is judged differently from the other four targets.

    The other four must be cell-identical: they are the same records rendered the
    same way, so any difference is a defect. This file is CURATED on one side and
    DERIVED on the other (amendment 0), so a difference is not a defect — it is a
    finding about the data. Membership drift here means a status is stale or a deal
    should be closed, and the fix belongs in the records, never in the export.
    So this target produces a review list with a cause per row, and never a verdict.
    """
    curated, derived = md_table(curated_path), md_table(derived_path)
    only_curated, only_derived = [], []
    causes, cause_err = {}, None
    # Causes need the base tables, which the read-only exporter role cannot see.
    # Degrade to an uncaused list rather than failing the whole reconciliation:
    # the four gated targets must still report even if this lookup is unavailable.
    if db_url:
        import psycopg
        try:
            with psycopg.connect(db_url) as conn, conn.cursor() as cur:
                cur.execute("""
                    select c.roster_ref,
                           c.merged_into is not null                             as merged,
                           cs.label, cs.is_active_pipeline,
                           exists (select 1 from deal d where d.client_id = c.id
                                   and d.outcome is null and d.phase <> 'closed') as open_deal
                      from client c join client_status cs on cs.slug = c.status
                     where c.roster_ref is not null
                """)
                causes = {r[0]: {"merged": r[1], "status": r[2], "flagged": r[3], "open_deal": r[4]}
                          for r in cur.fetchall()}
        except Exception as e:                        # noqa: BLE001 — reported, not swallowed
            cause_err = f"{type(e).__name__}: {e}"
    else:
        # No URL is the same epistemic state as a failed connection: we cannot
        # know causes. Without this, the loop below asserts "never imported"
        # for every row — a fabricated fact (caught live 2026-07-31).
        cause_err = "no reconcile DB URL set (CARR_RECONCILE_DB_URL / CARR_IMPORT_DB_URL)"

    for cid in sorted(set(curated) - set(derived)):
        f = causes.get(cid)
        if f is None and cause_err:
            why = "cause unavailable (no DB access this run)"
        elif f is None:
            why = "no client with this C-ID in the record layer (ref-less or never imported)"
        elif f["merged"]:
            why = "merge tombstone — this client was merged into another"
        elif not f["open_deal"] and not f["flagged"]:
            why = f"no open deal, and status '{f['status']}' is not flagged pipeline-active"
        else:
            why = "unexpected — has an open deal or a flagged status but did not render"
        only_curated.append((cid, (curated[cid].get("Name") or "").strip(), why))

    for cid in sorted(set(derived) - set(curated)):
        f = causes.get(cid)
        if f is None:
            why = ("cause unavailable (no DB access this run)" if cause_err
                   else "unexpected — rendered but absent from the cause lookup")
        else:
            why = ("has an open deal" if f.get("open_deal")
                   else f"status '{f.get('status')}' is flagged pipeline-active")
        why += " — on the book by the rules, absent from the curated file"
        only_derived.append((cid, (derived[cid].get("Name") or "").strip(), why))

    return {"curated": len(curated), "derived": len(derived), "cause_err": cause_err,
            "only_curated": only_curated, "only_derived": only_derived}


def norm(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)   # jsonb reorders keys; content equality
    if hasattr(v, "date"):
        v = v.date() if hasattr(v, "hour") else v
    s = str(v).strip()
    return {"TRUE": "Yes", "FALSE": "No", "True": "Yes", "False": "No", "Y": "Yes", "N": "No"}.get(s, s)


def diff_target(name, live, staged, ignore=()):
    live_rows, staged_rows = live["rows"], staged["rows"]
    only_live = sorted(set(live_rows) - set(staged_rows))
    only_staged = sorted(set(staged_rows) - set(live_rows))
    cell_diffs = []
    for k in sorted(set(live_rows) & set(staged_rows)):
        lr, sr = live_rows[k], staged_rows[k]
        for col in lr:
            if col in ignore or col not in sr:
                continue
            if norm(lr[col]) != norm(sr[col]):
                cell_diffs.append((k, col, norm(lr[col])[:60], norm(sr[col])[:60]))
    # Row-count equality is its own assertion, independent of the keyed diff: keys
    # can match perfectly while the files disagree on how many rows exist.
    return {"target": name,
            "live": len(live_rows), "staged": len(staged_rows),
            "live_total": live["total"], "staged_total": staged["total"],
            "live_blank": live["blank"], "staged_blank": staged["blank"],
            "count_mismatch": live["total"] != staged["total"],
            "only_live": only_live, "only_staged": only_staged, "cell_diffs": cell_diffs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=VAULT_DEFAULT)
    ap.add_argument("--staged", default=str(REPO / "out" / "exports"))
    a = ap.parse_args()
    vault, staged = Path(a.vault), Path(a.staged)

    results = []
    results.append(diff_target(
        "lead-registry.xlsx",
        sheet_dict(vault / "DNA/Leads/lead-registry.xlsx", "Registry", "Lead ID"),
        sheet_dict(staged / "lead-registry.xlsx", "Registry", "Lead ID")))
    results.append(diff_target(
        "client-roster.xlsx",
        sheet_dict(vault / "DNA/Clients/client-roster.xlsx", "Clients", "Client ID"),
        sheet_dict(staged / "client-roster.xlsx", "Clients", "Client ID")))
    results.append(diff_target(
        "vendors.xlsx",
        sheet_dict(vault / "DNA/Network/vendors.xlsx", "Vendors", "ID"),
        sheet_dict(staged / "vendors.xlsx", "Vendors", "ID")))
    # Target #8 (Wave 3, ORDER 25d). Checked only when it has actually been
    # staged: the pool migration has to land before the exporter can produce it,
    # and a missing staged file must not crash the reconcile for the other seven.
    # Keyed on License — the router's own identifier, measured present and unique
    # on all 9,320 rows, which is also what the importer keys idempotency on.
    router_staged = staged / "lead-router-2026-07-13.xlsx"
    if router_staged.exists():
        results.append(diff_target(
            "lead-router-2026-07-13.xlsx",
            sheet_dict(vault / "DNA/Leads/lead-router-2026-07-13.xlsx", "Lead Router", "License"),
            sheet_dict(router_staged, "Lead Router", "License")))

    live_deals = {d["name"]: d for d in json.loads(
        (vault / "DNA/Deal Management/panhandle-team-deals.json").read_text())["deals"]}
    staged_deals = {d["name"]: d for d in json.loads(
        (staged / "panhandle-team-deals.json").read_text())["deals"]}
    results.append(diff_target("panhandle-team-deals.json",
                               as_extract(live_deals), as_extract(staged_deals)))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# Reconciliation — staged exports vs live vault files — {stamp}", ""]
    total_diffs = 0
    for r in results:
        lines += [f"## {r['target']}: live {r['live']} keyed rows, staged {r['staged']}",
                  f"- TOTAL data rows: live {r['live_total']} / staged {r['staged_total']}"
                  + ("  ← **MISMATCH**" if r["count_mismatch"] else "")]
        for side in ("live", "staged"):
            for b in r[f"{side}_blank"]:
                shown = ", ".join(f"{k}={v}" for k, v in list(b.items())[:3] if v not in (None, ""))
                lines += [f"- **BLANK KEY in {side.upper()}** (unkeyed, excluded from the "
                          f"cell diff — counts as a discrepancy): {shown}"]
        if r["only_live"]:
            lines += [f"- ONLY IN LIVE ({len(r['only_live'])}): {', '.join(r['only_live'][:20])}"]
        if r["only_staged"]:
            lines += [f"- ONLY IN STAGED ({len(r['only_staged'])}): {', '.join(map(str, r['only_staged'][:20]))}"]
        lines += [f"- cell differences: {len(r['cell_diffs'])}"]
        for k, col, lv, sv in r["cell_diffs"][:80]:
            lines += [f"    - {k} · {col}: live '{lv}' -> staged '{sv}'"]
        if len(r["cell_diffs"]) > 80:
            lines += [f"    - ... and {len(r['cell_diffs']) - 80} more"]
        lines += [""]
        total_diffs += (len(r["cell_diffs"]) + len(r["only_live"]) + len(r["only_staged"])
                        + len(r["live_blank"]) + len(r["staged_blank"])
                        + (1 if r["count_mismatch"] else 0))
    # Fifth target: review list, not a gate. Deliberately outside total_diffs —
    # folding judgment calls into a pass/fail number is how a real gate stops
    # meaning anything.
    book = active_book_review(
        REPO / "frozen-sources" / "2026-07-30" / "clients-active.md",
        staged / "clients-active.md",
        os.environ.get("CARR_RECONCILE_DB_URL") or os.environ.get("CARR_IMPORT_DB_URL"))
    lines += ["## clients-active.md — CURATED vs DERIVED (Joe's review, not a gate)",
              f"- curated rows {book['curated']} / derived rows {book['derived']}",
              *([f"- ⚠ causes unavailable: {book['cause_err']}"] if book["cause_err"] else []),
              "- Each row below is a call: fix the data (stale status, deal to close) "
              "or accept the derived book. Never patch the exporter to match.", ""]
    lines += [f"### In curated, not derived ({len(book['only_curated'])})"]
    lines += [f"- **{c}** {n} — {w}" for c, n, w in book["only_curated"]] or ["- none"]
    lines += ["", f"### In derived, not curated ({len(book['only_derived'])})"]
    lines += [f"- **{c}** {n} — {w}" for c, n, w in book["only_derived"]] or ["- none"]
    lines += [""]

    out = REPO / "out" / f"reconciliation-{stamp}.md"
    out.write_text("\n".join(lines))
    print(f"report -> {out}")
    for r in results:
        flag = "  <<< COUNT MISMATCH" if r["count_mismatch"] else ""
        blanks = len(r["live_blank"]) + len(r["staged_blank"])
        print(f"  {r['target']}: rows live {r['live_total']} / staged {r['staged_total']}{flag}")
        print(f"      keyed {r['live']}/{r['staged']} · cell diffs {len(r['cell_diffs'])} · "
              f"only-live {len(r['only_live'])} · only-staged {len(r['only_staged'])} · "
              f"blank-key {blanks}")
    print(f"TOTAL discrepancies: {total_diffs}")


if __name__ == "__main__":
    main()
