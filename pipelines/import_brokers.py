"""ORDER 27(b): brokers.xlsx -> the record layer.

Read-then-report verdict: brokers fit the existing party shape with ZERO
vocabulary changes. A broker is a market counterparty (the agent on the other
side of a deal), never a referral vendor — this importer writes party rows
only. It creates NO vendor rows, NO party_link edges, NO building_ownership
or deal_participant rows. If a broker later becomes a real referral
relationship, that is a separate, human-decided `new-vendor` call.

Source: DNA/Network/brokers.xlsx, sheet 'Brokers', columns Broker ID, Name,
Firm, Area(s), Email, Phone, Owner, Relationship, Last Touch, Notes. Only
Broker ID / Name / Firm / Area(s) / Email carry data today; the rest are
empty on every row (measured, not assumed).

Two parties per row, at most:
  * an ORG party (kind='org'), one per distinct REAL firm name. Created via
    org_party_id(), the atomic find-or-create the 0059 migration built for
    exactly this: party_org_identity_uniq is a partial unique index and a
    blind insert on a repeated firm name raises unique_violation. This is
    the one place this importer intentionally does NOT follow import_wave1's
    party() idiom (which predates 0059 and inserts orgs blindly) — reusing a
    pre-0059 idiom here would break on the very first repeated firm name
    ("SVN" alone is 9 rows).
  * a PERSON party (kind='person'), one per broker row, org-linked, email
    carried, phone/city/state left null (not in the source). Inserted
    directly (wave1's party() pattern: insert always, flag collisions for
    HUMAN review, never auto-merge — Hovanian).

FIRM IS A DERIVED, UNVERIFIED FIELD. It was auto-derived from the email
domain and never confirmed. Two of its VALUES are personal-email artifacts,
not firms: "Gmail" and "Hotmail". Any row carrying one of those two values
gets its org left UNSET and is listed in the report as HELD for a one-line
human confirm — the broker still gets a person party, just no (wrong) firm
attached. Measured 2026-08-06: this is 6 broker rows (5 "Gmail" + 1
"Hotmail"), not the 2 rows the read-phase note describes — see the
docstring note at the bottom of main() and the final report for the
reconciliation of that count. A blank Firm (3 rows) is not an artifact and
is not held; it is simply no firm on record, same as any other missing cell.

AREAS HAS NO COLUMN. party carries no free-text note field (notes_path is a
markdown file pointer, not a place for a bulk one-liner) and the brief is
explicit: do not invent one. Area(s) rides in the event's summary instead —
genuine "src provenance", not a new column.

IDEMPOTENT by record_source (source_system='brokers.xlsx', external_key=
Broker ID), exactly wave1's seen()/mark() pattern: a re-run of the same row
is skipped before any party is touched. Org reuse is idempotent by
construction (org_party_id() is find-or-create); a re-run mints zero new org
rows for a firm already created on a prior run or already live in the vault.

Usage (via tools/db-tap.py, which supplies DATABASE_URL — never pass a DSN
on the command line):
  .venv/bin/python tools/db-tap.py run pipelines/import_brokers.py
  .venv/bin/python tools/db-tap.py run pipelines/import_brokers.py --apply
"""

import argparse
import os
import re
import sys
from pathlib import Path

import openpyxl
import psycopg

VAULT = Path(os.environ.get("CARR_VAULT") or "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/"
    "My Drive/CARR AI")
DEFAULT_SOURCE = VAULT / "DNA/Network/brokers.xlsx"
SHEET = "Brokers"
SOURCE_SYSTEM = "brokers.xlsx"

# The two email-domain artifacts the read phase flagged: not firms.
ARTIFACT_FIRMS = {"gmail", "hotmail"}

PLACEHOLDER_PHONE = "2056436555"


def s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def norm_phone(v):
    if v is None:
        return None
    raw = str(v).strip()
    if not raw:
        return None
    if re.sub(r"\D", "", raw)[-10:] == PLACEHOLDER_PHONE:
        return None
    return raw


def org_key(name):
    """Python mirror of the SQL org_identity_key() function (0059), used only
    to SIMULATE org_party_id() in dry-run mode without calling the real
    function — which performs a real INSERT when the org doesn't exist yet,
    and so must never run inside a dry run. --apply calls the real SQL
    function instead of this."""
    if name is None:
        return None
    t = name.strip()
    if not t:
        return None
    if t.startswith("("):
        return None
    low = t.lower()
    if re.search(r"\btbd\b", low) or re.search(r"\bunknown\b", low) or re.search(r"\bn/a\b", low):
        return None
    return re.sub(r"\s+", " ", low)


def read_rows(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[SHEET]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header = [s(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(v is not None and str(v).strip() for v in r):
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r))) if header[i]})
    return out


class Importer:
    def __init__(self, cur, apply_):
        self.cur = cur
        self.apply = apply_
        cur.execute("select id from actor where slug = 'system'")
        self.system = cur.fetchone()[0]

        # existing (pre-import) org identity, read via the real SQL function —
        # a pure read, safe in both modes.
        cur.execute("select id, name, org_identity_key(name) as key from party "
                    "where kind = 'org' and merged_into is null and deleted_at is null")
        self.existing_org_by_key = {}
        for pid, name, key in cur.fetchall():
            if key:
                self.existing_org_by_key.setdefault(key, []).append((pid, name))

        # existing (pre-import) person identity, for collision REPORTING only
        # (wave1's dup_candidates idiom — reported, never auto-merged).
        cur.execute("select id, name, email from party "
                    "where kind = 'person' and deleted_at is null")
        self.existing_person_by_email = {}
        self.existing_person_by_name = {}
        for pid, name, email in cur.fetchall():
            if email:
                self.existing_person_by_email.setdefault(email.strip().lower(), []).append((pid, name))
            if name:
                self.existing_person_by_name.setdefault(name.strip().lower(), []).append((pid, email))

        # batch-local indexes, wave1's exact idiom.
        self.batch_org_by_key = {}       # org_key -> party id (this run)
        self.batch_email_index = {}      # email.lower() -> (broker ref, name)
        self.batch_name_index = {}       # name.lower() -> (broker ref, name)

    # ---------- plumbing (wave1 idiom) ----------

    def seen(self, key):
        self.cur.execute(
            "select entity_id from record_source where source_system=%s and external_key=%s",
            (SOURCE_SYSTEM, key))
        r = self.cur.fetchone()
        return r[0] if r else None

    def mark(self, entity_id, key):
        self.cur.execute(
            "insert into record_source (entity_type, entity_id, source_system, external_key) "
            "values ('party',%s,%s,%s) on conflict do nothing",
            (entity_id, SOURCE_SYSTEM, key))

    def event(self, pid, summary):
        self.cur.execute(
            "insert into event (occurred_at, actor_id, verb, subject_type, subject_id, "
            "new_value, cause) values (now(), %s, 'import', 'party', %s, "
            "jsonb_build_object('summary', %s::text), 'import_migration')",
            (self.system, pid, summary))

    # ---------- org ----------

    def resolve_org(self, firm, plan):
        """Returns (org_id_or_None, held_bool). Never invents a firm; never
        touches the DB for a blank Firm."""
        firm = s(firm)
        if not firm:
            return None, False
        if firm.strip().lower() in ARTIFACT_FIRMS:
            plan["held"].append(firm)
            return None, True

        key = org_key(firm)
        if key is None:
            # a placeholder-shaped firm name (matches org_identity_key's own
            # exclusions) — same treatment the schema itself gives it.
            plan["placeholder_firms"].append(firm)
            return None, False

        if key in self.batch_org_by_key:
            plan["org_reused_in_batch"].append(firm)
            return self.batch_org_by_key[key], False

        if key in self.existing_org_by_key:
            existing = self.existing_org_by_key[key]
            plan["org_collision_existing"].append((firm, existing))
            if self.apply:
                self.cur.execute("select org_party_id(%s,%s) as id", (firm, self.system))
                oid = self.cur.fetchone()[0]
            else:
                oid = existing[0][0]
            self.batch_org_by_key[key] = oid
            return oid, False

        # brand-new org
        plan["org_new"].append(firm)
        if self.apply:
            self.cur.execute("select org_party_id(%s,%s) as id", (firm, self.system))
            oid = self.cur.fetchone()[0]
        else:
            oid = None  # not written yet
        self.batch_org_by_key[key] = oid
        return oid, False

    # ---------- person ----------

    def resolve_person_collisions(self, ref, name, email, plan):
        email_n = email.strip().lower() if email else None
        name_n = name.strip().lower()
        if email_n and email_n in self.existing_person_by_email:
            plan["collisions"].append(
                f"{ref}: '{name}' shares email {email_n} with existing "
                f"{self.existing_person_by_email[email_n]} — HUMAN review, not auto-merged")
        if name_n in self.existing_person_by_name:
            plan["collisions"].append(
                f"{ref}: '{name}' matches an existing party by exact name "
                f"{self.existing_person_by_name[name_n]} — HUMAN review, not auto-merged")
        if email_n and email_n in self.batch_email_index:
            plan["batch_dup_candidates"].append(
                f"{ref}: '{name}' shares email {email_n} with {self.batch_email_index[email_n]} "
                f"(this same import) — HUMAN review, not auto-merged")
        if name_n in self.batch_name_index:
            plan["batch_dup_candidates"].append(
                f"{ref}: '{name}' matches {self.batch_name_index[name_n]} by exact name "
                f"(this same import) — HUMAN review, not auto-merged")
        if email_n:
            self.batch_email_index.setdefault(email_n, []).append((ref, name))
        self.batch_name_index.setdefault(name_n, []).append((ref, name))

    def insert_person(self, name, org_id, email, phone):
        self.cur.execute(
            "insert into party (kind, name, org_id, phone, email, created_by, updated_by) "
            "values ('person',%s,%s,%s,%s,%s,%s) returning id",
            (name, org_id, norm_phone(phone), s(email), self.system, self.system))
        return self.cur.fetchone()[0]


def build_plan(rows, imp):
    plan = {
        "person_total": 0, "org_new": [], "org_reused_in_batch": [],
        "org_collision_existing": [], "placeholder_firms": [], "held": [],
        "collisions": [], "batch_dup_candidates": [], "no_email": [],
        "already_imported": [], "to_insert": [],
    }
    for row in rows:
        ref = s(row.get("Broker ID"))
        name = s(row.get("Name"))
        firm = s(row.get("Firm"))
        areas = s(row.get("Area(s)"))
        email = s(row.get("Email"))
        phone = s(row.get("Phone"))
        if not ref or not name:
            continue

        existing_entity = imp.seen(ref)
        if existing_entity:
            plan["already_imported"].append(ref)
            continue

        org_id, held = imp.resolve_org(firm, plan)
        imp.resolve_person_collisions(ref, name, email, plan)
        if not email:
            plan["no_email"].append(ref)

        plan["person_total"] += 1
        plan["to_insert"].append({
            "ref": ref, "name": name, "firm": firm, "areas": areas,
            "email": email, "phone": phone, "org_id": org_id, "held": held,
        })
    return plan


def run_apply(imp, plan):
    written_orgs = set()
    for oid in imp.batch_org_by_key.values():
        if oid:
            written_orgs.add(oid)
    written_persons = 0
    for item in plan["to_insert"]:
        pid = imp.insert_person(item["name"], item["org_id"], item["email"], item["phone"])
        imp.mark(pid, item["ref"])
        summary = f"imported {item['ref']} from brokers.xlsx (Firm: {item['firm'] or '(none)'}"
        if item["held"]:
            summary += " — HELD, email-domain artifact, not a real firm"
        summary += f"; Areas: {item['areas'] or '(none)'})"
        imp.event(pid, summary)
        written_persons += 1
    return written_persons, len(written_orgs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    url = os.environ.get("DATABASE_URL") or os.environ.get("CARR_IMPORT_DB_URL")
    if a.apply and not url:
        sys.exit("refusing --apply: no DATABASE_URL/CARR_IMPORT_DB_URL in the environment "
                 "(run via tools/db-tap.py, which supplies it)")
    if not url:
        sys.exit("DATABASE_URL/CARR_IMPORT_DB_URL not set — required even for a dry run, to "
                 "check collisions against existing parties")

    rows = read_rows(a.source)

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        imp = Importer(cur, a.apply)
        plan = build_plan(rows, imp)

        if a.apply:
            written_persons, written_orgs = run_apply(imp, plan)
            conn.commit()
        else:
            conn.rollback()  # defensive: build_plan issues reads only, never writes
            written_persons = written_orgs = None

    real_org_new = len(plan["org_new"])
    real_held = len(plan["held"])

    print(f"source: {a.source}")
    print(f"sheet data rows: {len(rows)}")
    print(f"mode: {'APPLY' if a.apply else 'DRY RUN (nothing written)'}")
    print()
    print("## Plan" if not a.apply else "## Written")
    if a.apply:
        print(f"  person rows written: {written_persons}")
        print(f"  org rows written (new, this run): {written_orgs}")
    else:
        print(f"  person parties to insert: {plan['person_total']}")
        print(f"  org parties to create (new, distinct real firms): {real_org_new}")
        print(f"  org reuses within this batch (same firm, later row): {len(plan['org_reused_in_batch'])}")
    print(f"  already imported (idempotent skip, record_source match): {len(plan['already_imported'])}")
    print(f"  rows with no Firm at all (org left null, not held): "
          f"{sum(1 for i in plan['to_insert'] if not i['firm'])}")
    print(f"  rows with no Email: {len(plan['no_email'])}")
    print()
    print(f"## Held for human confirm (Firm is an email-domain artifact, not a real firm): {real_held}")
    for item in plan["to_insert"]:
        if item["held"]:
            print(f"  - {item['ref']}: {item['name']} <{item['email']}> — Firm cell said "
                  f"'{item['firm']}', left org UNSET")
    print()
    print(f"## Org-name collisions against EXISTING parties (pre-import): "
          f"{len(plan['org_collision_existing'])}")
    for firm, existing in plan["org_collision_existing"]:
        print(f"  - '{firm}' already exists: {existing}")
    print()
    print(f"## Placeholder-shaped firm names (org_identity_key excludes, e.g. '(TBD)'): "
          f"{len(plan['placeholder_firms'])}")
    for f in plan["placeholder_firms"]:
        print(f"  - {f}")
    print()
    print(f"## Person collisions against EXISTING parties (name or email; HUMAN review, "
          f"never auto-merged): {len(plan['collisions'])}")
    for c in plan["collisions"]:
        print(f"  - {c}")
    print()
    print(f"## Duplicate candidates WITHIN this batch (HUMAN review, never auto-merged): "
          f"{len(plan['batch_dup_candidates'])}")
    for c in plan["batch_dup_candidates"]:
        print(f"  - {c}")
    print()
    if not a.apply:
        print(f"## New org names this run would create ({real_org_new}):")
        for f in sorted(plan["org_new"]):
            print(f"  - {f}")


if __name__ == "__main__":
    main()
