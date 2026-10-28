#!/usr/bin/env python3
"""renders-verify.py — the render-tamper detector (wave 1 C1, decision a317439f).

For every export target whose latest ok run stored a file_sha (0073), re-hash
the live vault file and compare. A mismatch means the render was modified by
something OTHER than the exporter after it was written — Drive web edit,
another device, partial sync, or a gate bypass — the exact class that silently
destroyed V-BNK-050.

Health-row contract per rule 590b11e1 (no metric without a bound action): the
output line names its response inline. On mismatch: re-export the named target
(CARR_EXPORT_LIVE=1 ./run.sh export --only <t> ) AND investigate what touched
the file, because a re-export alone erases the evidence of the edit.

Prints ONE line. Exit 0 OK / 1 mismatch / 2 SKIP.
"""

import hashlib
import os
import sys
import pathlib as _PATHLIB

# PHASE 4 GATE (2026-08-22). This tool's whole job is the legacy Drive vault, so
# it has no canonical mode: without a Drive root there is nothing for it to look
# at. It used to reach for a hardcoded root with no flag, no reason and no
# refusal, which is the ambient Drive selection Phase 4 exists to remove. Normal
# operation now refuses with the seam named (exit 69, which the nightly chain
# reads as BLOCKED); an acknowledged recovery exercise passes
# --recovery --reason WHY [--vault PATH].
sys.path.insert(0, str(_PATHLIB.Path(__file__).resolve().parents[1]))
from lib.credential_file import read_env_file  # noqa: E402
from lib.drive_recovery import require_legacy_vault  # noqa: E402

_RECOVERY = require_legacy_vault('record-native render verification (this compares vault renders against the record)')
sys.argv = [sys.argv[0], *_RECOVERY.args]
VAULT = str(_RECOVERY.vault)
ENV = os.path.expanduser("~/.config/carr/db.env")


def main() -> int:
    if not os.path.exists(ENV):
        print("SKIP: no db.env")
        return 2
    # EXPORTER credential, not jobs: export_run belongs to the exporter lane
    # (0006), and information_schema only shows a role the columns it can read
    # — the jobs role produced a false "0073 not applied" SKIP on first live
    # run, the same role-visibility class as the vendor_category grant bug.
    url = None
    for key in ("CARR_DB_EXPORTER_URL", "CARR_DB_JOBS_URL"):
        url = read_env_file(ENV, syntax="prefix-double-single",
                            duplicates="first", names=(key,)).get(key)
        if url:
            break
    if not url:
        print("SKIP: no exporter/jobs credential in db.env")
        return 2
    try:
        import psycopg
        c = psycopg.connect(url)
        cur = c.cursor()
        cur.execute("select 1 from information_schema.columns "
                    "where table_name='export_run' and column_name='file_sha'")
        if not cur.fetchone():
            print("SKIP: migration 0073 not applied here yet")
            return 2
        # latest ok run per target that carries a file hash
        cur.execute("""
            select distinct on (target) target, file_sha
              from export_run
             where status='ok' and file_sha is not null
             order by target, ran_at desc""")
        rows = cur.fetchall()
    except Exception as e:
        print(f"SKIP: read failed ({type(e).__name__}: {str(e).splitlines()[0]})")
        return 2

    # map target -> vault path via the exporter registry (parsed like the gate does)
    # Script-relative, NOT expanduser("~/carr-system"). On a clone outside $HOME
    # the exporters import below failed, this printed "SKIP: registry
    # unreadable" and returned 2 — so the render-TAMPER detector reported itself
    # skipped instead of running. ci.sh's own doctrine is that skipped is not
    # passed; a path bug must not be what decides which one this is.
    sys.path.insert(0, os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")))
    try:
        from exporters.targets import TARGETS
        paths = {k: rel for k, (rel, _fn) in TARGETS.items()}
    except Exception as e:
        print(f"SKIP: registry unreadable ({type(e).__name__})")
        return 2

    # THE CUTOFF (fired 2026-08-19). A retired .md render is ABSENT ON PURPOSE —
    # it was moved to _to_delete staging and the exporter no longer writes it. Its
    # last ok run still carries a file_sha, so without this the detector counted
    # every retired render as GONE and told the reader to re-export a target that
    # now prints RETIRED: a permanent red row whose remedy does nothing, which is
    # the exact way a real tamper finding gets trained into background noise.
    # Reads the SAME flag function the exporter itself gates on — one contract,
    # so this row can never disagree with what the exporter actually did.
    retired = 0
    try:
        from exporters.run_exports import md_renders_retired
        if md_renders_retired():
            gone = [k for k, rel in paths.items() if rel.lower().endswith(".md")]
            retired = len(gone)
            paths = {k: rel for k, rel in paths.items() if k not in gone}
    except Exception:
        pass          # fail OPEN, same as the exporter: keep checking everything

    checked = mismatched = missing = 0
    bad = []
    for target, stored in rows:
        rel = paths.get(target)
        if not rel:
            continue          # loop-file targets etc. register elsewhere; covered as adopted
        p = os.path.join(VAULT, rel)
        if not os.path.exists(p):
            missing += 1
            bad.append(f"{target}(GONE)")
            continue
        checked += 1
        live = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if live != stored:
            mismatched += 1
            bad.append(target)

    if mismatched or missing:
        print(f"WARN renders-verify — {mismatched} tampered + {missing} missing of {checked} hashed "
              f"renders: {', '.join(bad[:5])} · on breach: re-export the target AND investigate "
              f"the edit first (a re-export erases the evidence)")
        return 1
    tail = f", {retired} retired at the cutoff" if retired else ""
    print(f"OK renders-verify — {checked} hashed renders match their export bytes "
          f"({len(paths) - checked} not yet hashed; they gain a hash at next export"
          f"{tail}) · on breach: re-export + investigate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
