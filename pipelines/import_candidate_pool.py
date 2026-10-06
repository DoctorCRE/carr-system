"""Import DNA/Leads/lead-router-2026-07-13.xlsx into candidate_pool (ORDER 25(b)).

WHAT THIS IS. The router is the market map: 9,320 licensed practitioners across
six territory counties, segmented by buying signal, built 2026-07-13. Until now
it has been a file the board displays and `lead-promote.py` shortlists from.
Nothing could act on it as records. This lifts every row into the pool tier that
Joe's Wave 3 ruling created, where the board, the radar lanes and the promote
verb can all reach it.

NEVER-PRE-QUALIFY IS THE CONSTRAINT, NOT A PREFERENCE. Every data row in the
sheet becomes a pool row. A row matching an existing lead or client is marked
`suppressed_dup` and pointed at what it matched — it is still a row, it still
carries its segment and its contact fields, and it still counts. Nothing is
filtered on the way in, ever. The done-test is an exact equality: pool rows for
this source == sheet data rows.

THE DEDUP SEMANTICS ARE REUSED, NOT INVENTED. Two existing gates read this same
router file today and this importer runs both of them:

  1. The registry suppressor (Joe, 2026-07-14; renewal-radar-sop.md; implemented
     in generators/build-renewal-feed.py). Practice match = 2+ shared distinctive
     tokens after a healthcare/geo stopword list, or equal non-trivial token sets.
     Contact match = registry first AND last both present, last name >= 4 chars,
     both appearing in the candidate's text. The GENERIC stoplist below is that
     file's, verbatim.
  2. The promotion dedupe gate (pipelines/lead-promote.py). Exact email match,
     lowercased, against every registry, roster and deal email — "name alone
     misses the spelling drift this vault is full of (Kleinmeyer/Kleinmayer,
     Lindsay/Lindsey, Connor/Conner)".

ONE DELIBERATE SOFTENING, ruled by the Wave 3 design: the radar suppressor DROPS
a do-not-contact match ("never resurface a lead Joe has explicitly stopped
touching"). The pool cannot drop anything, so the drop becomes the
`dup_do_not_contact` flag. The row is kept, marked, and never re-presented.

TWO PRECISION CORRECTIONS, made because the numbers demanded them and REPORTED
rather than buried (both are reversible with --strict-suppression, which runs the
source semantics verbatim):

  (i) The practice-token rule matches on 2+ shared distinctive tokens. Against
      167 CoStar tenants that was precise enough. Against 9,320 router rows on
      2026-07-31 it produced 30 matches and nearly all were wrong: "panama city"
      (a city the stoplist does not cover) tied ten unrelated practices to L-195;
      "dentistry"+"implant" tied five to L-188. Those matches are kept as
      dup_tier 'review' — pointed at, visible, still on the board — instead of
      suppressing rows Joe has never seen. A false suppression is never-pre-qualify
      failing quietly, which is the one failure this order cannot ship.

 (ii) The contact rule reads first = cn[0] and last = cn[-1] of the registry
      name. That is exact for "Dorian Brenner" and wrong for the shapes this data
      actually holds: "Lawrence and Lawrence" yields first == last == "lawrence",
      matching all 16 router rows containing that surname; "Lisa Bexley (Gameday
      Men's Health)" yields last = "health"; "Sara Holt-Mackenzie, ARNP"
      yields last = "arnp". Three guards, each the original's own intent applied
      at the other end of the string: parentheticals are stripped before parsing,
      trailing credentials are dropped exactly as leading honorifics already are,
      and first == last is not a first/last pair. 19 matches turned on the
      first == last defect alone.

Neither correction loosens anything. Both make the gate stricter about what it
calls a duplicate, and nothing stops being recorded.

SOURCE OF THE COMPARISON SET. The gates above read lead-registry.xlsx and
client-roster.xlsx. This reads `v_export_leads` and `v_export_clients`, which are
the views those two files are GENERATED from — the same columns, one step closer
to the record. Same semantics, no file dependency.

IDEMPOTENT by (source, source_key) — source_key is the router's License number,
measured present and unique on all 9,320 rows. A rerun writes 0 new rows and
reports what it skipped.

Usage:
  CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_candidate_pool
      [--source PATH] [--dry-run] [--refresh-suppression]
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import openpyxl
import psycopg

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from sheets import header_map, data_rows          # noqa: E402

VAULT = Path(os.environ.get("CARR_VAULT") or "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
DEFAULT_SOURCE = VAULT / "DNA/Leads/lead-router-2026-07-13.xlsx"
SHEET = "Lead Router"
SOURCE_SLUG = "lead-router"

# Every column the sheet carries. The 13 in ROUTER_REQUIRED are validated by
# header_map; these four extras are read defensively (present today, and the
# export passes them straight back through source_row).
EXTRA_COLS = ["SUNBIZ entities", "Licensed", "Typical Term (est)", "License"]

SCORE_BASIS_NONE = (
    "unscored at import: lead-router-2026-07-13 carries no score column. The "
    "routing judgment lives in SEGMENT / THE PLAY, which are imported verbatim. "
    "A scoring pass is a separate decision — inventing numbers here would put a "
    "fabricated rank in front of Joe at the board.")

# ── the suppressor's stopword list, verbatim from generators/build-renewal-feed.py ──
GENERIC = {"dr","md","do","dds","dmd","pa","arnp","aprn","the","of","and","llc","pllc","inc","pc","co",
 "center","centre","centers","clinic","clinics","group","associates","assoc","associate","health","healthcare",
 "medical","care","services","service","specialists","specialist","dermatology","dental","orthopedic","orthopaedic",
 "pediatric","pediatrics","family","internal","medicine","vision","eye","surgery","surgical","plastic","hearing",
 "skin","wellness","institute","physicians","physician","office","offices","laser","rehabilitation","aid","extended",
 "rehab","assisted","living","nursing","memory","home","st","saint","north","south","gulf","coast","oral","states",
 "cardiology","cardiac","urgent","primary","womens","women","mens","childrens","child","kids","behavioral","mental",
 "therapy","physical","occupational","imaging","radiology","oncology","cancer","urology","neurology","dermatologic",
 "pensacola","milton","navarre","pace","cantonment","breeze","perdido","jay","bagdad","gonzalez","molino","escambia",
 "santarosa","rosa","santa","county","fl","florida","al","alabama","area","nw","ne","sw","se","llp","pllcs","ppec"}

DNC_RE = re.compile(r"do not contact|do not nudge|no further outreach|inbound only|"
                    r"no nudges|sequence closed|no follow")
DNC_STAGES = {"paused", "closed", "lost", "dead", "do not contact"}

# Leading honorifics the source drops, plus the trailing credentials and generation
# suffixes it never anticipated. Same idea, other end of the string.
HONORIFICS = {"dr", "mr", "mrs", "ms"}
CREDENTIALS = {"md", "do", "dds", "dmd", "dpm", "od", "dc", "pa", "arnp", "aprn", "np",
               "rn", "lmhc", "lcsw", "phd", "psyd", "facs", "faacs", "faap", "pt", "ot",
               "slp", "esq", "jr", "sr", "ii", "iii", "iv"}
PAREN_RE = re.compile(r"\([^)]*\)")


def val(x):
    """The suppressor's own value normalizer: '' for None and the literal 'None'."""
    if isinstance(x, (datetime, date)):
        return x.isoformat()[:10]
    s = "" if x is None else str(x).strip()
    return "" if s.lower() == "none" else s


def distinct(s):
    """Distinctive tokens — build-renewal-feed.py's function, unchanged."""
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()
            if t not in GENERIC and len(t) > 2}


def name_parts(contact, strict=False):
    """(first, last) of a registry/roster name.

    strict=True is the source's exact behaviour: drop honorifics, take cn[0] and
    cn[-1]. Default adds the precision guards documented at the top — the
    parenthetical strip, the trailing-credential drop, and the rejection of names
    that are not name pairs at all. Returning ('', '') disables the contact rule
    for that record, which is exactly what the source does when last is missing.
    """
    text = contact.lower()
    if not strict:
        text = PAREN_RE.sub(" ", text)
    cn = [t for t in re.sub(r"[^a-z ]", " ", text).split() if t not in HONORIFICS]
    if not strict:
        while len(cn) > 2 and cn[-1] in CREDENTIALS:
            cn.pop()
    first = cn[0] if cn else ""
    last = cn[-1] if len(cn) > 1 else ""
    if not strict:
        # first == last is a practice name, not a name pair ("Lawrence and
        # Lawrence"). A "surname" drawn from the healthcare stopword list is the
        # same failure in a different costume: C-116's roster Name parsed to
        # first "bay", last "surgery", which then matched any router row tied to
        # a surgery centre in Bay county. Rejecting both is the source's own
        # stated intent — "avoid short common ones" — applied to the shapes this
        # roster actually contains.
        if first == last or first in GENERIC or last in GENERIC:
            return "", ""
    return first, last


def load_known(cur, strict=False):
    """The comparison set: every lead and every client, shaped like the suppressor wants.

    Columns are the export views' — i.e. exactly the columns the xlsx-reading
    gates use, because those files are generated from these views.
    """
    known = []
    cur.execute('select "Lead ID", "Owner", "Stage", "Contact Name", "Practice", '
                '"Next Action", "Notes", "Email" from v_export_leads')
    for ref, owner, stage, contact, practice, nexta, notes, email in cur.fetchall():
        first, last = name_parts(val(contact), strict)
        blob = (val(nexta) + " " + val(notes)).lower()
        known.append({
            "type": "lead", "ref": val(ref), "owner": val(owner), "stage": val(stage),
            "first": first, "last": last, "ptoks": distinct(practice),
            "email": val(email).lower(),
            "dnc": bool(DNC_RE.search(blob)) or val(stage).lower() in DNC_STAGES,
        })
    cur.execute('select "Client ID", "Owner", "Status", "Name", "Practice / Entity", "Email" '
                'from v_export_clients')
    for ref, owner, status, name, practice, email in cur.fetchall():
        first, last = name_parts(val(name), strict)
        known.append({
            "type": "client", "ref": val(ref), "owner": val(owner), "stage": val(status),
            "first": first, "last": last, "ptoks": distinct(practice),
            "email": val(email).lower(),
            # A client is never "do not contact" by virtue of being a client; the
            # flag exists for leads Joe has explicitly stopped touching.
            "dnc": False,
        })
    return known


def match_known(name, org, email, known, by_email, strict=False):
    """The three rules, in precision order. Returns (record, basis, tier) or three Nones.

    Email first: it is exact, and it is the gate that catches the spelling drift
    the token matcher was built to survive. Contact-name second. Practice-token
    last, and in the default mode it yields the 'review' tier rather than
    suppression — see the module docstring for the measurement behind that.
    """
    e = (email or "").lower().strip()
    if e and e in by_email:
        g = by_email[e]
        return g, f"email exact match to {g['type']} {g['ref']} ({e})", "suppressed"
    tks = distinct(name) | distinct(org)
    ctoks = set(re.sub(r"[^a-z ]", " ", (str(name) + " " + str(org)).lower()).split())
    weak = None
    for g in known:
        shared = {t for t in (g["ptoks"] & tks) if len(t) >= 4}
        pmatch = (len(shared) >= 2) or (g["ptoks"] and g["ptoks"] == tks and len(g["ptoks"]) >= 1)
        nmatch = bool(g["first"] and g["last"] and len(g["last"]) >= 4
                      and g["first"] in ctoks and g["last"] in ctoks)
        pbasis = (f"practice-token match to {g['type']} {g['ref']} "
                  f"(shared: {', '.join(sorted(shared)) or 'equal token sets'})")
        nbasis = f"contact-name match to {g['type']} {g['ref']} ({g['first']} {g['last']})"
        if strict:
            # Strict mode preserves the source's practice-token suppression
            # semantics, but it cannot waive the system-wide name-only ban.
            if pmatch:
                return g, pbasis, "suppressed"
            if nmatch:
                if weak is None:
                    weak = (g, "REVIEW, not suppressed — " + nbasis, "review")
            continue
        if nmatch:
            # A name is one identifying field, even when it has both a first
            # and a last token.  Suppressing on it silently turns a plausible
            # duplicate into an invisible merge decision, which is precisely
            # what the candidate pool is forbidden to do. Exact email remains
            # a stable identity key; a name-only hit stays visible for
            # confirmation.
            if weak is None:
                weak = (g, "REVIEW, not suppressed — " + nbasis, "review")
        if pmatch and weak is None:
            weak = (g, "REVIEW, not suppressed — " + pbasis, "review")
    return weak if weak else (None, None, None)


def read_sheet(path):
    """Every data row of the router, header-validated. Returns (headers, [row dicts])."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[SHEET]
    required = ["SEGMENT", "THE PLAY", "Owns?", "Name", "Profession", "Lic Yrs", "Age Band",
                "# at Address", "Practice Address", "City", "County", "Email", "Phone"]
    c = header_map(ws, required, f"{Path(path).name}[{SHEET}]")
    for extra in EXTRA_COLS:
        if extra not in c:
            raise SystemExit(f"SCHEMA HALT: router column {extra!r} is gone; "
                             "the export would silently drop it. Stop and report.")
    headers = sorted(c, key=lambda k: c[k])
    out = []
    for i, r in enumerate(data_rows(ws), start=1):
        if not any(x is not None and str(x).strip() != "" for x in r):
            continue                      # a truly empty spacer row is not data
        out.append((i, {h: r[c[h]] for h in headers}))
    wb.close()
    return headers, out


def jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-suppression", action="store_true",
                    help="re-evaluate the dedup gate on rows that already exist "
                         "(a lead created since the last run can newly suppress a pool row). "
                         "Never flips a promoted row and never un-suppresses by itself.")
    ap.add_argument("--strict-suppression", action="store_true",
                    help="run the renewal-radar suppressor's semantics VERBATIM: no "
                         "precision guards, and a practice-token match suppresses rather "
                         "than flags for review. Measured 2026-07-31 to produce ~49 wrong "
                         "suppressions in 9,320 rows; here so the call is Joe's, not the "
                         "importer's.")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("CARR_IMPORT_DB_URL not set")

    headers, rows = read_sheet(a.source)
    rep = {"inserted": 0, "suppressed": 0, "review": 0, "dnc": 0, "skipped_existing": 0,
           "refreshed": 0, "no_name": [], "no_key": [], "dup_key": [], "by_segment": {}}

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id from actor where slug = 'system'")
        sys_id = cur.fetchone()[0]
        known = load_known(cur, a.strict_suppression)
        by_email = {}
        for g in known:                       # first writer wins; leads load before clients
            if g["email"] and g["email"] not in by_email:
                by_email[g["email"]] = g
        cur.execute("select source_key, status from candidate_pool where source = %s",
                    (SOURCE_SLUG,))
        existing = dict(cur.fetchall())

        seen, pending = set(), []
        for seq, r in rows:
            name = val(r.get("Name"))
            key = val(r.get("License"))
            if not name:
                rep["no_name"].append(seq)
                continue
            if not key:
                # The stop rule: an absent ID scheme parks the mapping table. A
                # single blank key in an otherwise keyed file is reported per-row
                # rather than silently synthesised.
                rep["no_key"].append(f"row {seq}: {name}")
                continue
            if key in seen:
                rep["dup_key"].append(f"row {seq}: {name} ({key})")
                continue
            seen.add(key)

            seg = val(r.get("SEGMENT"))
            rep["by_segment"][seg] = rep["by_segment"].get(seg, 0) + 1

            if key in existing:
                rep["skipped_existing"] += 1
                if a.refresh_suppression and existing[key] == "pool":
                    g, basis, tier = match_known(name, r.get("SUNBIZ entities"),
                                                 val(r.get("Email")), known, by_email,
                                                 a.strict_suppression)
                    if g and tier == "suppressed" and not a.dry_run:
                        cur.execute("""
                            update candidate_pool
                               set status='suppressed_dup', dup_tier='suppressed',
                                   dup_subject_type=%s, dup_ref=%s, dup_basis=%s,
                                   dup_do_not_contact=%s, updated_by=%s
                             where source=%s and source_key=%s and status='pool'
                        """, (g["type"], g["ref"], basis, g["dnc"], sys_id, SOURCE_SLUG, key))
                        rep["refreshed"] += 1
                continue

            # The org-side text is SUNBIZ entities, NOT the street address. The
            # suppressor compares practice-name tokens; feeding it an address would
            # match two unrelated practices on a shared street name.
            g, basis, tier = match_known(name, r.get("SUNBIZ entities"), val(r.get("Email")),
                                         known, by_email, a.strict_suppression)
            status = "suppressed_dup" if tier == "suppressed" else "pool"
            if tier == "suppressed":
                rep["suppressed"] += 1
                if g["dnc"]:
                    rep["dnc"] += 1
            elif tier == "review":
                rep["review"] += 1

            pending.append((
                SOURCE_SLUG, key, seq,
                json.dumps({h: jsonable(r[h]) for h in headers}),
                name,
                # The router has no practice-name column. SUNBIZ entities is the
                # nearest thing it carries: the entities this person is tied to,
                # verbatim including the file's own [REGISTERED AGENT, 2015]
                # annotations. Kept verbatim rather than parsed — parsing it is a
                # decision nobody has made, and source_row holds it either way.
                val(r.get("SUNBIZ entities")) or None,
                val(r.get("Profession")) or None,
                val(r.get("Practice Address")) or None,
                val(r.get("City")) or None, val(r.get("County")) or None,
                # The router is a six-county FL territory file (Escambia, Santa
                # Rosa, Okaloosa, Walton, Bay, Leon — measured, not assumed). It
                # carries no state column; stating FL is reading the file.
                "FL",
                val(r.get("Email")) or None, val(r.get("Phone")) or None,
                seg or None, val(r.get("THE PLAY")) or None,
                SCORE_BASIS_NONE, status, tier,
                g["type"] if g else None, g["ref"] if g else None, basis,
                bool(g and g["dnc"] and tier == "suppressed"), sys_id, sys_id))
            rep["inserted"] += 1

        # One batched write rather than 9,320 round trips. Over a remote Neon
        # endpoint the row-at-a-time form does not finish inside a sane timeout;
        # psycopg3's executemany pipelines the whole batch. Same statement, same
        # conflict clause, same idempotency — only the number of round trips changes.
        if pending and not a.dry_run:
            cur.executemany("""
                insert into candidate_pool
                    (source, source_key, source_seq, source_row, name, org_name,
                     vertical, address, city, county, state, email, phone,
                     segment, segment_play, score, score_basis, status, dup_tier,
                     dup_subject_type, dup_ref, dup_basis, dup_do_not_contact,
                     created_by, updated_by)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        null,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (source, source_key) do nothing
            """, pending)

        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()

        cur.execute("select count(*) from candidate_pool where source = %s", (SOURCE_SLUG,))
        total_after = cur.fetchone()[0]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# lead-router -> candidate_pool — {stamp}"
             + ("  (DRY RUN — nothing written)" if a.dry_run else ""), "",
             f"Source: `{a.source}`", f"Sheet data rows: {len(rows)}",
             f"Pool rows for source '{SOURCE_SLUG}' after this run: {total_after}", "",
             "## Written",
             f"- inserted: {rep['inserted']}",
             f"- of those, marked suppressed_dup: {rep['suppressed']} "
             f"(do-not-contact among them: {rep['dnc']} — KEPT and flagged, never dropped)",
             f"- of those, flagged dup_tier 'review' and STILL PRESENTED: {rep['review']}",
             f"- already present, skipped (idempotent): {rep['skipped_existing']}",
             f"- suppression refreshed on existing rows: {rep['refreshed']}", "",
             "## Not written (reported, never guessed)"]
    for k, title in (("no_name", "rows with a blank Name"),
                     ("no_key", "rows with a blank License (no source key)"),
                     ("dup_key", "License repeated in the file (second ignored)")):
        v = rep[k]
        lines.append(f"- {title}: {len(v)}" + (f" — {', '.join(map(str, v[:20]))}" if v else ""))
    lines += ["", "## Segment distribution (presented, never filtered)"]
    for seg, n in sorted(rep["by_segment"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {n:>5}  {seg or '(blank)'}")
    out = REPO / "out" / f"prospect-pool-import-{stamp}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"report -> {out}")
    print(f"  sheet rows {len(rows)} · inserted {rep['inserted']} · "
          f"suppressed_dup {rep['suppressed']} (dnc {rep['dnc']}) · review {rep['review']} · "
          f"skipped-existing {rep['skipped_existing']} · pool total {total_after}"
          + ("  [STRICT: source semantics verbatim]" if a.strict_suppression else ""))
    if not a.dry_run and total_after != len(rows):
        sys.exit(f"DONE-TEST FAILED: {total_after} pool rows vs {len(rows)} sheet data rows. "
                 "Every sheet row must land — a suppressed_dup is still a row.")


if __name__ == "__main__":
    main()
