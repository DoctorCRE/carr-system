#!/usr/bin/env python3
"""ORDER 36 step 7 — import the 23 hand-maintained dossiers' analysis prose as
dated `kind=analysis` activity rows.

DRY RUN BY DEFAULT. --apply writes, and refuses to run unless migration 0028 is
present in schema_migrations (the vocabulary and the render views must exist
first). Run it through db-tap so no DSN reaches a shell command:

    .venv/bin/python tools/db-tap.py run pipelines/import_dossier_analysis.py
    .venv/bin/python tools/db-tap.py run pipelines/import_dossier_analysis.py --apply
    .venv/bin/python tools/db-tap.py --branch rehearse-0028 run \
        pipelines/import_dossier_analysis.py --apply --only Nephrova.md

WHAT IT DOES NOT DO — the stop rules, in code:
  * It never guesses a date or an author. A section whose date or author cannot
    be read off the file's OWN stamps is imported WITH ITS TEXT INTACT (nothing
    is lost) and flagged on the review list, with the reason recorded per row.
  * A row's occurred_at falls back to the file's `Last updated:` stamp, or
    failing that the file's mtime (which IS flagged — mtime is not a claim the
    file makes). Author falls back to the file's `owner:` frontmatter per Joe's
    2026-08-01 ruling, and `source` records WHICH level supplied it —
    import / import_file_stamp / import_unattributed — so the render can say
    "per file stamp, not this section" instead of printing an inherited name as
    though the section had stamped it.
  * It writes one row per H2 section plus one for the pre-H2 header block. It
    does not merge, summarise, rewrite or reflow prose — the body is the
    section's bytes.

CHUNKING, and why H2: every one of the 23 files organises itself by `## `
headings — dated addenda in the deal files (HarborlinePelvicTherapy,
BayviewDPC-Whitfield) and topical sections in the narrative ones
(LumoraDentalGroup, Okafor). H3s stay INSIDE their parent section; splitting on
them would shred a single argument across rows.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exporters.targets import DOSSIER_DIR, DOSSIER_FILES  # noqa: E402

VAULT = Path(os.environ.get("CARR_VAULT") or "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

H2 = re.compile(r"^## +(.*)$")
ISO_DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
LAST_UPDATED = re.compile(r"^Last updated:\s*(.+?)\s*$", re.M)
# Authors are only ever read off an explicit stamp the file itself carries.
AUTHOR = re.compile(r"\((?:by\s+)?(Joe|Dell|Claude)\b", re.I)
# The WHOLE owner line, not the first token. Joe's dictated stamp for
# LumoraDentalGroup is "Shared, Dell originated" — a \S+ capture would have taken
# "Shared," and thrown away both the attribution and the fact that it is shared.
FM_OWNER = re.compile(r"^owner:\s*(.+?)\s*$", re.M)
# Which actor a stamp names. A compound stamp still has exactly one party who
# ORIGINATED it, and that is who the row is attributed to; the full text rides
# along so the render never flattens "Shared, Dell originated" to bare "dell".
ACTOR_IN_STAMP = re.compile(r"\b(joe|dell)\b", re.I)

HEADER_TITLE = "Dossier header (legacy import)"


def parse_date(text):
    m = ISO_DATE.search(text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc), "iso"
        except ValueError:
            return None, None
    m = US_DATE.search(text)
    if m:
        mo, day, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        if not yr:
            return None, None          # bare 7/30 — the YEAR is a guess. Never guessed.
        yr = int(yr) + 2000 if int(yr) < 100 else int(yr)
        try:
            return datetime(yr, mo, day, tzinfo=timezone.utc), "us"
        except ValueError:
            return None, None
    return None, None


def parse_file(path: Path):
    """-> rows. Each row carries its own PROVENANCE, not just its values.

    THE OWNER FALLBACK (ratified by Joe, 2026-08-01). The first pass flagged 84
    of 104 rows, overwhelmingly for a missing per-section author stamp — a review
    gate nobody could work through. The ruling: a section with no stamp of its
    own inherits the file-level `owner:` frontmatter, recorded with a DIFFERENT
    provenance string so nothing ever reads as more certain than it is.

    Provenance is tracked per field and travels with the row:
      date   — 'section heading' | 'file stamp (Last updated)' | 'file mtime'
      author — 'section stamp'   | 'file stamp (owner)'        | 'none'

    A row is FLAGGED when a value cannot be traced to something the file itself
    asserts: no owner stamp anywhere, or a date that had to come from filesystem
    mtime. mtime is deliberately NOT treated as a file stamp — it records when
    the bytes last changed, which is routinely months off the content's date and
    is exactly the kind of confident-looking wrong fact the stop rule exists to
    keep off a dossier.
    """
    text = path.read_text()
    lines = text.splitlines()

    fallback_dt = None
    m = LAST_UPDATED.search(text)
    if m:
        fallback_dt, _ = parse_date(m.group(1))
    if fallback_dt is None:
        fallback_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        fallback_prov = "file mtime"
    else:
        fallback_prov = "file stamp (Last updated)"

    fm = FM_OWNER.search(text)
    file_owner = fm.group(1) if fm else None

    # split on H2
    chunks: list[tuple[str | None, list[str]]] = []
    cur_title: str | None = None
    cur_body: list[str] = []
    for ln in lines:
        m2 = H2.match(ln)
        if m2:
            chunks.append((cur_title, cur_body))
            cur_title, cur_body = m2.group(1).strip(), []
        else:
            cur_body.append(ln)
    chunks.append((cur_title, cur_body))

    rows = []
    for title, body in chunks:
        body_text = "\n".join(body).strip("\n")
        is_header = title is None
        if is_header and not body_text.strip():
            continue
        title = HEADER_TITLE if is_header else title

        flags = []
        dt, _kind = parse_date(title)
        if dt is not None:
            date_prov = "section heading"
        else:
            dt, date_prov = fallback_dt, fallback_prov
            if date_prov == "file mtime":
                where = "the header block" if is_header else "this section's heading"
                flags.append(f"no date on {where} and the file carries no "
                             f"`Last updated:` stamp; fell back to file mtime")

        if title is None:
            raise RuntimeError(f"section title unexpectedly missing for {path}")
        am = AUTHOR.search(title)
        if am:
            author = am.group(1).lower()
            author_stamp, author_prov = am.group(1), "section stamp"
        elif file_owner:
            hit = ACTOR_IN_STAMP.search(file_owner)
            if hit:
                author, author_stamp = hit.group(1).lower(), file_owner
                author_prov = "file stamp (owner)"
            else:
                # An owner line naming nobody the actor table knows. Do not guess
                # which human it means — that is the whole stop rule.
                author, author_stamp, author_prov = None, file_owner, "none"
                flags.append(f"the file's `owner:` stamp ({file_owner!r}) names no "
                             f"actor this system knows")
        else:
            author, author_stamp, author_prov = None, None, "none"
            flags.append("no author stamped on this section and the file carries "
                         "no `owner:` frontmatter")

        rows.append({"title": title, "body": body_text, "occurred_at": dt,
                     "author": author, "author_stamp": author_stamp,
                     "date_prov": date_prov, "author_prov": author_prov,
                     "flags": flags})
    return rows


def _is_production(url: str) -> bool:
    """True if this DSN is production. Asks Neon, never the caller.

    --rehearse exists to write rows, so it has to prove it is pointed somewhere
    disposable. Comparing hosts against the production connection string Neon
    itself hands back is the only check that cannot be defeated by a typo in a
    branch name.
    """
    import subprocess
    from urllib.parse import urlparse
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    neonctl = os.path.join(repo, "mcp-server", "node_modules", ".bin", "neonctl")
    out = subprocess.run(
        [neonctl, "connection-string", "production", "--project-id",
         "steep-field-48688294", "--role-name", "neondb_owner"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:"
             + os.environ.get("PATH", "")})
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("could not resolve production's DSN to compare against — refusing "
                 "to guess whether this is production")
    return urlparse(url).hostname == urlparse(out.stdout.strip()).hostname


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rehearse", action="store_true",
                    help="permit --apply against a NEON BRANCH only, to produce the "
                         "review packet's diffs; refuses production")
    ap.add_argument("--only", help="one dossier basename, for the file-by-file gate (step 8)")
    a = ap.parse_args()

    # The roster is a gitignored local file (exporters/dossier_roster.py,
    # WR-000049). Without it DOSSIER_FILES is empty, and an empty set would
    # "import" nothing and then fail the notes_path count below with a
    # misleading "the set moved". Say what is actually missing instead.
    if not DOSSIER_FILES:
        from exporters.dossier_roster import missing_message
        sys.exit(missing_message())

    files = [a.only] if a.only else DOSSIER_FILES
    if a.only and a.only not in DOSSIER_FILES:
        sys.exit(f"{a.only} is not one of the {len(DOSSIER_FILES)} dossiers")

    # The gate runs on the DRY RUN too, not only on --apply. A dry run whose
    # counts are quoted into a review packet needs to have been checked against
    # the database those counts will be imported into — otherwise the packet
    # describes a plan for a schema that may not exist. Skipped silently when
    # there is no DSN, so the parser stays runnable offline.
    url = os.environ.get("DATABASE_URL")
    if url:
        import psycopg
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("select 1 from schema_migrations where filename like '0028%%'")
            if not cur.fetchone():
                sys.exit("migration 0028 is not applied to this database — STOP. "
                         "The analysis vocabulary and render views do not exist yet.")
            cur.execute("select count(*) from client where notes_path is not null")
            n = cur.fetchone()[0]
            if n != len(DOSSIER_FILES):
                sys.exit(f"the database carries {n} dossier notes_path rows but "
                         f"DOSSIER_FILES lists {len(DOSSIER_FILES)} — the set moved. "
                         "STOP and reconcile before importing.")
            cur.execute("select count(*) from activity where kind='analysis'")
            existing = cur.fetchone()[0]
        print(f"gate: 0028 applied · {n} notes_path rows match DOSSIER_FILES · "
              f"{existing} analysis row(s) already present\n")

    total, flagged, per_file = 0, 0, []
    prov = {"date": {}, "author": {}}
    for name in files:
        p = VAULT / DOSSIER_DIR / name
        rows = parse_file(p)
        f = sum(1 for r in rows if r["flags"])
        for r in rows:
            prov["date"][r["date_prov"]] = prov["date"].get(r["date_prov"], 0) + 1
            prov["author"][r["author_prov"]] = prov["author"].get(r["author_prov"], 0) + 1
        total += len(rows)
        flagged += f
        per_file.append((name, len(rows), f))

    w = max(len(n) for n, _, _ in per_file)
    print(f"{'dossier'.ljust(w)}  rows  flagged")
    for n, c, f in per_file:
        print(f"{n.ljust(w)}  {c:4d}  {f:7d}")
    print(f"{'TOTAL'.ljust(w)}  {total:4d}  {flagged:7d}")

    print("\nprovenance — date")
    for k, v in sorted(prov["date"].items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")
    print("provenance — author")
    for k, v in sorted(prov["author"].items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")

    if not a.apply:
        print("\nDRY RUN — nothing written.")
        return

    if not url:
        sys.exit("no DATABASE_URL — run through tools/db-tap.py")

    # Hold removed 2026-08-01 by the supervisor seat after Joe's packet review
    # and GO (flat render + owner lines ruled; execution log has the record).
    # --rehearse remains available for branch runs.

    # --rehearse: import into a BRANCH so the review packet can show Joe the real
    # post-import diffs. It proves it is not production rather than trusting the
    # caller — a flag that only promises to be safe is not a safety mechanism.
    if a.rehearse and _is_production(url):
        sys.exit("--rehearse was pointed at PRODUCTION. Refusing. Run it through "
                 "tools/db-tap.py --branch <name>.")

    import psycopg
    written = skipped = 0
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # WHO THE RENDER CALLS THE AUTHOR is actor_id — v_export_dossier_analysis
        # selects act.slug. So the actor has to BE the author the file names, or
        # the owner-fallback ruling is lost the moment the row lands: every
        # imported row would read "· system" and the file's own stamps would
        # survive nowhere.
        #
        # Actor = the named human (section stamp or file `owner:`), else 'system'.
        # The CONFIDENCE of that name rides `source`, which is already the
        # provenance column on this table ('stated', 'import', 'mail_ingest',
        # 'call_recording'), so no DDL and no new vocabulary table:
        #   import               — the section stamped its own author
        #   import_file_stamp    — inherited from the file's `owner:` frontmatter
        #   import_unattributed  — no owner anywhere; actor is 'system'
        # The render prints the distinction (see _dossier_stamp), so a reader can
        # never mistake an inherited stamp for one the section actually carried.
        # FLAGGED for ratification: three source values where one existed.
        cur.execute("select slug, id from actor")
        actors = {s: i for s, i in cur.fetchall()}
        if "system" not in actors:
            sys.exit("no 'system' actor to attribute unattributed rows to")

        def source_for(r):
            """The provenance label that rides `source` on the imported row.

            A compound file stamp carries its FULL text after the colon, so the
            render can print "dell (per file stamp 'Shared, Dell originated')"
            instead of silently reducing a shared, Dell-originated client to one
            name. No DDL: `source` is already this table's free-text provenance
            column ('stated', 'import', 'mail_ingest', 'call_recording').
            """
            if r["author_prov"] == "section stamp":
                return "import"
            if r["author_prov"] == "none":
                return "import_unattributed"
            stamp = (r.get("author_stamp") or "").strip()
            if stamp.lower() == (r["author"] or ""):
                return "import_file_stamp"
            return f"import_file_stamp:{stamp}"[:200]
        for name in files:
            rel = f"{DOSSIER_DIR}/{name}"
            cur.execute("select client_id from v_export_dossier_subject where rel_path=%s", (rel,))
            got = cur.fetchone()
            if not got:
                print(f"  SKIP {name}: no subject row"); skipped += 1; continue
            client_id = got[0]
            # POSITIONAL recorded_at (Fable ruling, 2026-08-01). The render picks
            # "current analysis" with `order by occurred_at desc, recorded_at
            # desc, id`. Sections carrying no date of their own inherit the
            # file's single date, and a whole file imports inside ONE
            # transaction, so now() was identical for every row — which dropped
            # the tie onto a random uuid and made "current" arbitrary in 19 of
            # the 23 files (Okafor's `Changelog` won rank 1 that way).
            #
            # Stamping recorded_at one microsecond apart in FILE ORDER makes the
            # tie break by position in the source document: the LAST section in
            # the file ranks first. In the dated-addendum dossiers that is
            # genuinely the newest entry, because their addenda run down the
            # page. It is also true on its face — the rows really were recorded
            # in this order, so nothing here is a fabricated fact.
            file_rows = parse_file(VAULT / DOSSIER_DIR / name)
            base = datetime.now(timezone.utc)
            for ordinal, r in enumerate(file_rows):
                actor_id = actors.get(r["author"] or "", actors["system"])
                cur.execute(
                    "insert into activity (occurred_at, recorded_at, actor_id, kind, "
                    "                      summary, detail, client_id, source) "
                    "values (%s, %s, %s, 'analysis', %s, %s, %s, %s)",
                    (r["occurred_at"], base + timedelta(microseconds=ordinal), actor_id,
                     r["title"], r["body"], client_id, source_for(r)))
                written += 1
        conn.commit()
    # Keyed off a.rehearse — the SAME flag that gated the connection above (line
    # 282's _is_production check only runs, and only refuses, when a.rehearse is
    # set). Before this fix the message was unconditional and always said
    # "REHEARSAL ... Production untouched", which was a lie on a plain --apply
    # run: with no --rehearse, writes go straight to whatever DATABASE_URL
    # resolves to, production in the normal invocation. Fixed 2026-08-06, loop
    # #189.
    if a.rehearse:
        print(f"\nREHEARSAL import: {written} analysis rows written to the branch, "
              f"{skipped} file(s) skipped. Production untouched.")
    else:
        print(f"\nPRODUCTION import: {written} analysis rows WRITTEN TO PRODUCTION, "
              f"{skipped} file(s) skipped.")


if __name__ == "__main__":
    main()
