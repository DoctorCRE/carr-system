#!/usr/bin/env python3
"""cutover-readiness.py — is <partner> actually ready to boot from the store?

WHY THIS EXISTS (Phase 1, 2026-08-13, the August 21 cutover). The cutover moves
Dell's BOOT PATH from the generated compiled-rules markdown files to the
store's standing-context verb. Proving that is safe was, until this script,
a manual ritual someone had to remember to run and eyeball — and the record
layer's own house rule is that a manual check nobody runs is not a check
(rules-live-check.py's whole reason for existing is the same lesson one layer
up: three green manual signals and a rule that bound nobody). This makes the
readiness question a PREDICATE: read-only, no arguments, deterministic,
wired into the nightly chain so drift shows up before the 21st rather than on
it.

WHAT "READY" MEANS, per partner, four things all agreeing:
  1. A LIVE standing-context call (through the same local path a real session
     boots through — tools/call-verb.py -> mcp-server/local-verb.mjs) returns
     rule counts.
  2. Those counts match an INDEPENDENT query against the store's own active-
     rule view (exporters.targets._fetch_rules — the exporter's own fetch, not
     a second reimplementation of it; same reasoning as rules-live-check.py:
     a second query is a second contract, and the two would drift exactly the
     way the doc and the export drifted to produce the 2026-08-04 defect).
  3. The partner's compiled-rules RENDER (the fallback file DELL boots from
     until 2026-08-21, and JOE's emergency fallback after) declares a count
     that also agrees. Disagreement here means the fallback is stale and
     would mislead if the store were ever unreachable.
  4. DOCTRINE is reachable through the same local path (doctrine-index).

THE ONE THING THIS SCRIPT CANNOT DO: prove #1 and #4 for a partner who is not
THIS machine. Phase 1 closed the caller-supplied-identity hole on purpose
(mcp-server/local-verb.mjs, 2026-08-13) — there is no argv slug anymore, and
there must not be, because faking one here would be reopening the exact hole
that change closed for the sake of a health check. Identity is derived from
~/.config/carr/local-actor.json, written once per machine by
bin/set-local-actor.sh, so on Joe's Mac this can only ever prove #1/#4 for
"joe"; run it on Dell's Mac and it proves them for "dell". Checks #2 and #3
are store/file facts with no identity in them, so they run for BOTH partners
from either machine — that half of the predicate is never partial.

A partner who cannot be live-checked from this machine is reported
PARTIAL-HERE, not READY and not NOT READY: nothing failed, something merely
was not asked. PARTIAL-HERE does not fail the exit code. An actual
disagreement anywhere (store vs render, live call vs store, doctrine
unreachable, store unreachable) is NOT READY and does fail it.

Writes out/cutover-readiness.json (the delegate-pattern artifact
tools/health-check.py reads, same shape as rules-live-check's stdout but
structured for a second reader) and prints the same verdict as text.

  ./.venv/bin/python ops/cutover-readiness.py

Exit 0 = every partner is READY or PARTIAL-HERE (nothing checkable failed).
Exit 2 = at least one partner has an actual disagreement or an unreachable
         store/verb — see the printed finding for which.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from exporters.common import VAULT, connect          # noqa: E402
from exporters.targets import _fetch_rules            # noqa: E402
from lib import record_call                          # noqa: E402

IDENTITY_FILE = Path.home() / ".config" / "carr" / "local-actor.json"
OUT_PATH = REPO / "out" / "cutover-readiness.json"

PARTNERS = ("joe", "dell")

# label, personal_to slug (None = shared, checked once and applied to both
# partners), vault-relative render path — identical set to rules-live-check.py
# on purpose: this reuses that file's own reasoning below rather than
# restating it.
AUDIENCES = {
    "shared": (None, "DNA/compiled-rules-shared.md"),
    "joe":    ("joe", "00_Context/compiled-rules-joe.md"),
    "dell":   ("dell", "DNA/compiled-rules-dell.md"),
}

# Same two regexes as rules-live-check.py, same reason: a render with zero
# active rules (compiled-rules-dell.md today) skips the bold header and
# survives only in the italic exported-footer line.
DECLARED = re.compile(r"\*\*(\d+)\s+active rule\(s\)")
DECLARED_FOOTER = re.compile(r"·\s*(\d+)\s+active rule\(s\)\*")


def declared_count(path: Path):
    if not path.exists():
        return None
    text = path.read_text()
    m = DECLARED.search(text)
    if m:
        return int(m.group(1))
    m = DECLARED_FOOTER.search(text)
    return int(m.group(1)) if m else None


def format_age(then: datetime, now: datetime) -> str:
    delta = now - then
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m ago"
    if hours < 48:
        return f"{hours:.1f}h ago"
    return f"{delta.days}d ago"


def fetch_boot_evidence(conn, cur, partner: str, now: datetime):
    """The most recent STORE-FIRST boot recorded for `partner` — the latest
    tool_read_call row for verb='standing-context' with that sponsor
    (migration 0108, Phase 1). This is what turns "did a session boot from
    the store, and whose?" from a manual test into a line in this report.
    Table absence (0108 not yet applied here) is reported as unknown, not a
    crash — this script must keep answering every other question it already
    answers even before that migration lands everywhere it runs."""
    try:
        cur.execute(
            "select client_id, via, ok, created_at from tool_read_call "
            "where verb = 'standing-context' and sponsoring_human_slug = %s "
            "order by created_at desc limit 1", (partner,))
        row = cur.fetchone()
    except Exception as e:
        conn.rollback()  # a failed query aborts the transaction; reset it so later
                          # queries in this same connection (the other partner, or
                          # anything after) are not poisoned by this one's failure.
        return {"table_reachable": False, "note": f"tool_read_call not queryable here ({type(e).__name__}): {e}"}
    if not row:
        return {"table_reachable": True, "seen": False,
                "note": "no store-first boot recorded yet"}
    client_id, via, ok, created_at = row
    return {"table_reachable": True, "seen": True, "ok": ok, "via": via, "client": client_id,
            "at": created_at.isoformat(), "age": format_age(created_at, now)}


def resolve_local_identity():
    """Whichever partner slug this machine is set up as, or None with why not."""
    try:
        data = json.loads(IDENTITY_FILE.read_text())
    except FileNotFoundError:
        return None, f"no identity file at {IDENTITY_FILE} — bin/set-local-actor.sh has never run here"
    except (OSError, ValueError) as e:
        return None, f"{IDENTITY_FILE} unreadable/invalid ({e})"
    slug = (data.get("actor_slug") or "").strip()
    if not slug:
        return None, f"{IDENTITY_FILE} has no actor_slug"
    if slug not in PARTNERS:
        return slug, f"resolves to '{slug}', which is not a known partner slug {PARTNERS}"
    return slug, None


def classify_partner(partner, is_local, render_ok, reason=None,
                     live_ok=None, live_agrees=None, identity_agrees=None, doctrine_ok=None):
    """Pure decision: (status_string, counts_as_failure). Factored out of main()
    so ops/cutover-readiness-selftest.py can exercise every branch without a
    live database or a local-actor identity file — the DB/verb calls are the
    part that cannot be faked without reopening the identity hole Phase 1
    closed (see the module docstring), but the DECISION on top of their
    results is ordinary logic and untestable-in-place is not the same as
    untestable."""
    if not render_ok:
        return "NOT READY", True
    if not is_local:
        return (f"PARTIAL-HERE (store+render only — {reason}; run this on "
                 f"{partner}'s Mac to prove the live boot path)"), False
    ready = bool(live_ok and live_agrees and identity_agrees and doctrine_ok)
    return ("READY" if ready else "NOT READY"), (not ready)


def call_verb(verb, args=None):
    """Run one read verb through the same local path a real session boots
    through. Returns (ok, payload_or_error_str). Never raises — a verb call
    that fails is a finding, not a crash, same as every other check in this
    file's family (rules-live-check.py, guard-selftest.py)."""
    result = record_call.call_verb(verb, args or {}, timeout=60)
    return (True, result.reply) if result.ok else (False, result.describe())


def main():
    generated_at = datetime.now(timezone.utc).isoformat()
    local_slug, identity_note = resolve_local_identity()

    # ---- store-side facts: run once, apply to both partners, no identity in it ----
    try:
        conn = connect()
    except SystemExit as e:
        # exporters.common.connect() sys.exit()s with a message when the
        # exporter credential is absent — house convention (rules-live-check.py):
        # a missing credential is a SKIP for the whole run, not a per-partner
        # failure, because nothing at all could be verified.
        result = {"generated_at": generated_at, "skip": str(e)}
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(result, indent=2))
        print(f"SKIP cutover-readiness: {e}")
        return 0

    store_counts = {}
    render_checks = {}
    boot_evidence = {}
    with conn, conn.cursor() as cur:
        for label, (slug, rel) in AUDIENCES.items():
            n = len(_fetch_rules(cur, slug))
            store_counts[label] = n
            path = VAULT / rel
            declared = declared_count(path)
            render_checks[label] = {
                "path": rel,
                "exists": path.exists(),
                "store_count": n,
                "declared_count": declared,
                "agrees": declared is not None and declared == n,
            }
        # Boot evidence is a store FACT, not tied to which machine this runs
        # on (unlike live_call below) — it reports what ANY machine's session
        # already recorded, which is the whole point: Dell's cutover question
        # answered from Joe's Mac, or vice versa, with no manual test either way.
        now = datetime.now(timezone.utc)
        for partner in PARTNERS:
            boot_evidence[partner] = fetch_boot_evidence(conn, cur, partner, now)

    overall_ok = True
    partner_reports = {}

    for partner in PARTNERS:
        shared_r = render_checks["shared"]
        personal_r = render_checks[partner]
        render_ok = shared_r["agrees"] and personal_r["agrees"]

        is_local = (partner == local_slug)
        report = {
            "store_counts": {"shared": store_counts["shared"], "personal": store_counts[partner]},
            "render": {"shared": shared_r, "personal": personal_r},
            "render_agrees": render_ok,
            "checked_live_here": is_local,
            "boot_evidence": boot_evidence[partner],
        }

        if not is_local:
            reason = (f"this machine's identity is '{local_slug}'" if local_slug
                      else f"this machine has no resolved identity ({identity_note})")
            report["live_call"] = {"ran": False, "reason": reason}
            report["doctrine"] = {"ran": False, "reason": reason}
            status, is_failure = classify_partner(partner, is_local=False, render_ok=render_ok, reason=reason)
            report["status"] = status
            if is_failure:
                overall_ok = False
            partner_reports[partner] = report
            continue

        # ---- the live half: only provable for this machine's own partner ----
        sc_ok, sc = call_verb("standing-context")
        live = {"ran": True, "ok": sc_ok}
        counts_agree = identity_agrees = False
        if sc_ok:
            live_shared = len(sc.get("shared_rules") or [])
            live_personal = len(sc.get("personal_rules") or [])
            live["shared"] = live_shared
            live["personal"] = live_personal
            live["sponsoring_human_id"] = (sc.get("identity") or {}).get("sponsoring_human_id")
            counts_agree = (live_shared == store_counts["shared"]
                            and live_personal == store_counts[partner])
            identity_agrees = live["sponsoring_human_id"] == partner
            live["agrees_with_store"] = counts_agree
            live["identity_agrees"] = identity_agrees
        else:
            live["error"] = sc
        report["live_call"] = live

        doc_ok, doc = call_verb("doctrine-index")
        doctrine = {"ran": True, "ok": doc_ok}
        if not doc_ok:
            doctrine["error"] = doc
        else:
            doctrine["documents"] = len(doc.get("documents") or [])
        report["doctrine"] = doctrine

        status, is_failure = classify_partner(
            partner, is_local=True, render_ok=render_ok,
            live_ok=sc_ok, live_agrees=counts_agree, identity_agrees=identity_agrees,
            doctrine_ok=doc_ok)
        report["status"] = status
        if is_failure:
            overall_ok = False
        partner_reports[partner] = report

    result = {
        "generated_at": generated_at,
        "this_machine": {"resolved_identity": local_slug, "note": identity_note},
        "partners": partner_reports,
        "overall_ready": overall_ok,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2))

    # ---- human-readable verdict ----
    print(f"cutover readiness — {generated_at}")
    print(f"  this machine resolves to: {local_slug or '(unresolved — ' + str(identity_note) + ')'}")
    for partner in PARTNERS:
        r = partner_reports[partner]
        print(f"  {partner:<5} {r['status']}")
        print(f"        store: {r['store_counts']['shared']} shared + "
              f"{r['store_counts']['personal']} {partner}-personal")
        sr, pr = r["render"]["shared"], r["render"]["personal"]
        print(f"        render shared  ({sr['path']}): declared={sr['declared_count']} "
              f"vs store={sr['store_count']} — {'agrees' if sr['agrees'] else 'DISAGREES'}")
        print(f"        render personal({pr['path']}): declared={pr['declared_count']} "
              f"vs store={pr['store_count']} — {'agrees' if pr['agrees'] else 'DISAGREES'}")
        if r["checked_live_here"]:
            lc = r["live_call"]
            if lc.get("ok"):
                print(f"        live standing-context: {lc['shared']} shared + {lc['personal']} personal, "
                      f"sponsoring_human_id={lc.get('sponsoring_human_id')} — "
                      f"{'agrees with store' if lc.get('agrees_with_store') and lc.get('identity_agrees') else 'DISAGREES'}")
            else:
                print(f"        live standing-context: FAILED — {lc.get('error')}")
            dc = r["doctrine"]
            print(f"        doctrine-index: {'ok, ' + str(dc.get('documents')) + ' documents' if dc.get('ok') else 'FAILED — ' + str(dc.get('error'))}")
        else:
            print(f"        live standing-context / doctrine-index: not checked — {r['live_call']['reason']}")
        be = r["boot_evidence"]
        if not be.get("table_reachable"):
            print(f"        store-first boot: unknown — {be.get('note')}")
        elif not be.get("seen"):
            print(f"        store-first boot: no store-first boot recorded yet")
        else:
            print(f"        store-first boot: {be['age']} ({be['at']}), via={be.get('via')}, "
                  f"client={be.get('client')}, ok={be.get('ok')}")
    print(f"  overall: {'READY (or PARTIAL-HERE only)' if overall_ok else 'NOT READY — see DISAGREES/FAILED lines above'}")

    return 0 if overall_ok else 2


if __name__ == "__main__":
    sys.exit(main())
