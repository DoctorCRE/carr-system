"""Wave 1 import: registry + roster + vendors + deals JSON -> the record layer.

Idempotent by construction: every record carries a record_source row keyed
(source_system, external_key); a re-run skips what exists. Rehearses on a
Neon BRANCH; the real run happens at freeze against production.

Rules enforced here (addendum/A-series + standing data rules):
  * provenance on every record (record_source), source_row preserved on deals
  * NO auto-merge (Hovanian): parties reuse ONLY on exact-email match
    within this import; everything fuzzier lands in the dup-candidate report
  * the placeholder phone 205-643-6555 is never stored as a contact
  * unknown vocab (stages, phases, deal types) is INSERTED and REPORTED,
    never guessed into an existing slug
  * ref sequences seeded above imported max (no max+1 races)
  * one import_migration event per record, actor = system
  * a missing figure is left null and reported, never invented

Usage:
  CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_wave1 [--vault PATH]
Writes out/import-report-<stamp>.md for the reconciliation packet.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl
import psycopg

VAULT_DEFAULT = ("/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/"
                 "My Drive/CARR AI")
PLACEHOLDER_PHONE = "2056436555"
DEAL_TYPE_MAP = {  # JSON txn / roster Deal Type -> deal.deal_type check values
    "lease": "lease", "purchase": "purchase", "sale": "purchase",
    "renewal": "renewal", "sale-leaseback": "sale_leaseback",
    "sale leaseback": "sale_leaseback", "build-to-suit": "build_to_suit",
    "build to suit": "build_to_suit", "bts": "build_to_suit",
}

report: dict[str, Any] = {"counts": {}, "vocab_added": [], "dup_candidates": [], "owners_unmapped": set(),
                          "type_fallbacks": [], "skipped": [], "notes": []}


def norm_phone(v):
    """Verbatim except the placeholder. '(enrich)' and friends are real
    workflow markers the live files carry; only 205-643-6555 is redacted."""
    if v is None:
        return None
    raw = str(v).strip()
    if not raw:
        return None
    if re.sub(r"\D", "", raw)[-10:] == PLACEHOLDER_PHONE:
        report["notes"].append("placeholder phone 205-643-6555 encountered and NOT stored")
        return None
    return raw


def s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def dt(v, field, ref):
    """Coerce to a date or return (None, raw). Non-dates ('M2M', 'TBD') are
    preserved by the caller in notes + report — never fake-dated, never lost."""
    if v is None or v == "":
        return None, None
    if hasattr(v, "date"):
        return v.date() if hasattr(v, "hour") else v, None
    raw = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return raw[:10], None
    report["notes"].append(f"{ref}: non-date value '{raw}' in {field} — kept in notes, date left null")
    return None, raw


class Importer:
    def __init__(self, cur, vault):
        self.cur = cur
        self.vault = Path(vault)
        cur.execute("select id, slug from actor")
        self.actors = {slug: aid for aid, slug in cur.fetchall()}
        self.system = self.actors["system"]
        self.email_index = {}   # lowercased email -> party id (exact-match reuse only)
        self.name_index = {}    # (name.lower, city.lower) -> party id, for dup REPORTING only

    # ---------- plumbing ----------

    def seen(self, source_system, key):
        self.cur.execute("select entity_id from record_source where source_system=%s and external_key=%s",
                         (source_system, key))
        r = self.cur.fetchone()
        return r[0] if r else None

    def mark(self, entity_type, entity_id, source_system, key):
        self.cur.execute("insert into record_source (entity_type, entity_id, source_system, external_key) "
                         "values (%s,%s,%s,%s) on conflict do nothing",
                         (entity_type, entity_id, source_system, key))

    def event(self, subject_type, subject_id, summary):
        self.cur.execute(
            "insert into event (occurred_at, actor_id, verb, subject_type, subject_id, "
            "new_value, cause) values (now(), %s, 'import', %s, %s, %s, 'import_migration')",
            (self.system, subject_type, subject_id, json.dumps({"summary": summary})))

    def actor_for(self, owner_str):
        if not owner_str:
            return None
        slug = str(owner_str).strip().lower()
        if slug in self.actors:
            return self.actors[slug]
        report["owners_unmapped"].add(str(owner_str))
        return None

    def vocab(self, table, slug_value):
        """Ensure a vocab slug exists; report additions. Returns the slug."""
        if slug_value is None:
            return None
        slug = re.sub(r"[^a-z0-9]+", "_", str(slug_value).strip().lower()).strip("_")
        if not slug:
            return None
        self.cur.execute(f"select 1 from {table} where slug=%s", (slug,))
        if not self.cur.fetchone():
            self.cur.execute(f"insert into {table} (slug, label) values (%s, %s)",
                             (slug, str(slug_value).strip()))
            report["vocab_added"].append(f"{table}: {slug} ('{slug_value}')")
        return slug

    def party(self, name, *, kind="person", org_id=None, phone=None, email=None,
              city=None, state=None, county=None, specialty=None, src=""):
        name = s(name)
        if not name:
            return None
        email_n = s(email).lower() if s(email) else None
        if email_n and email_n in self.email_index:
            report["dup_candidates"].append(f"{src}: '{name}' shares email with an earlier party "
                                            f"— HUMAN review via confirm-merge, NOT auto-merged")
        key = (name.lower(), (s(city) or "").lower())
        if key in self.name_index:
            report["dup_candidates"].append(f"{src}: '{name}' ({city or '?'}) matches an earlier "
                                            f"party by name+city — HUMAN review, not merged")
        self.cur.execute(
            "insert into party (kind, name, org_id, phone, email, city, state, county, specialty, "
            "created_by, updated_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (kind, name, org_id, norm_phone(phone), s(email), s(city), s(state), s(county),
             s(specialty), self.system, self.system))
        pid = self.cur.fetchone()[0]
        if email_n:
            self.email_index[email_n] = pid
        self.name_index.setdefault(key, pid)
        return pid

    # ---------- sources ----------

    def sheet_rows(self, rel, sheet):
        wb = openpyxl.load_workbook(self.vault / rel, read_only=True, data_only=True)
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        header = [s(h) for h in rows[0]]
        out = []
        for r in rows[1:]:
            if not any(v is not None and str(v).strip() for v in r):
                continue
            out.append({header[i]: r[i] for i in range(min(len(header), len(r))) if header[i]})
        return out

    def import_vendors(self):
        n = 0
        for sheet, oom in [("Vendors", False), ("Out of Market", None)]:
            rows = self.sheet_rows("DNA/Network/vendors.xlsx", sheet)
            for row in rows:
                ref = s(row.get("ID"))
                if not ref:
                    report["skipped"].append(f"vendors/{sheet}: row without ID ({s(row.get('Name'))})")
                    continue
                if self.seen("vendors.xlsx", ref):
                    continue
                if oom is None and "ID" not in row:
                    continue
                org = self.party(row.get("Company"), kind="org", src=f"vendors:{ref}") \
                    if s(row.get("Company")) else None
                pid = self.party(row.get("Name"), org_id=org, phone=row.get("Phone"),
                                 email=row.get("Email"), state=row.get("State"),
                                 src=f"vendors:{ref}")
                if pid and s(row.get("Title")):
                    self.cur.execute("update party set title=%s where id=%s and title is null",
                                     (s(row.get("Title")), pid))
                if not pid:
                    report["skipped"].append(f"vendors/{sheet}: {ref} has no Name")
                    continue
                stage = self.vocab("vendor_stage", row.get("Stage"))
                self.cur.execute(
                    "insert into vendor (vendor_ref, party_id, category, verticals, stage, owner_id, "
                    "owner_label, referral_active, territory, offers, seeking, rivalry_group, "
                    "originated, enrich, out_of_market, intro_notes, links_label, created_by, updated_by) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                    (ref, pid, s(row.get("Category")) or "uncategorized",
                     [v.strip() for v in str(row.get("Vertical") or "").split(",") if v.strip()] or None,
                     stage, self.actor_for(row.get("Owner")), s(row.get("Owner")),
                     (str(row.get("Referral-active?")).strip().lower() in ("yes","y","true")) if s(row.get("Referral-active?")) is not None else None,
                     s(row.get("Territory")), s(row.get("Offers")), s(row.get("Seeking")),
                     s(row.get("Rivalry Group")), s(row.get("Originated / Referred")),
                     (str(row.get("Enrich?")).strip().lower() in ("yes","y","true")) if s(row.get("Enrich?")) is not None else None,
                     sheet == "Out of Market", s(row.get("Notes")), s(row.get("Links")),
                     self.system, self.system))
                vid = self.cur.fetchone()[0]
                self.mark("vendor", vid, "vendors.xlsx", ref)
                lt, _lt_raw = dt(row.get("Last Touch"), "Last Touch", ref)
                if lt:
                    self.cur.execute(
                        "insert into activity (occurred_at, actor_id, kind, summary, vendor_id, source) "
                        "values (%s,%s,'note','last-touch stamp (imported)',%s,'import')",
                        (lt, self.actor_for(row.get("Owner")) or self.system, vid))
                if s(row.get("Next Step")):
                    self.cur.execute(
                        "insert into next_action (subject_type, subject_id, owner_id, description, "
                        "created_by) values ('vendor',%s,%s,%s,%s) on conflict do nothing",
                        (vid, self.actor_for(row.get("Owner")) or self.system,
                         s(row.get("Next Step")), self.system))
                self.event("vendor", vid, f"imported {ref} from vendors.xlsx/{sheet}")
                n += 1
        report["counts"]["vendors"] = n

    def import_leads(self):
        rows = self.sheet_rows("DNA/Leads/lead-registry.xlsx", "Registry")
        n = 0
        for row in rows:
            ref = s(row.get("Lead ID"))
            if not ref:
                report["skipped"].append(f"registry: row without Lead ID ({s(row.get('Contact Name'))})")
                continue
            if self.seen("lead-registry", ref):
                continue
            org = self.party(row.get("Practice"), kind="org", city=row.get("City/Market"),
                             county=row.get("County"), src=f"registry:{ref}") \
                if s(row.get("Practice")) else None
            pid = self.party(row.get("Contact Name") or row.get("Practice"), org_id=org,
                             phone=row.get("Phone"), email=row.get("Email"),
                             city=row.get("City/Market"), county=row.get("County"),
                             specialty=row.get("Specialty"), src=f"registry:{ref}")
            if not pid:
                report["skipped"].append(f"registry: {ref} has neither Contact Name nor Practice")
                continue
            stage = self.vocab("lead_stage", row.get("Stage")) or self.vocab("lead_stage", "New")
            rb_due, rb_raw = dt(row.get("Report-Back Due"), "Report-Back Due", ref)
            drip_on, drip_raw = dt(row.get("Drip Added"), "Drip Added", ref)
            ele, ele_raw = dt(row.get("Est-Lease-Event"), "Est-Lease-Event", ref)
            date_in, di_raw = dt(row.get("Date In"), "Date In", ref)
            notes = s(row.get("Notes"))
            self.cur.execute(
                "insert into lead (registry_ref, party_id, stage, segment, source_type, source_detail, "
                "report_back_due, report_back_due_raw, drip_campaign, drip_added, drip_added_raw, sf_deal, notes_path, notes, "
                "est_lease_event, est_lease_event_raw, event_source, event_confidence, owner_id, owner_label, "
                "created_at, created_by, updated_by) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "coalesce(%s::timestamptz, now()),%s,%s) returning id",
                (ref, pid, stage, s(row.get("Segment")), s(row.get("Source Type")),
                 s(row.get("Source Detail (V-ID / event / referrer)")),
                 rb_due, rb_raw, s(row.get("Drip Campaign")), drip_on, drip_raw,
                 s(row.get("SF Deal")), s(row.get("Detail File")), notes,
                 ele, ele_raw, s(row.get("Event-Source")),
                 s(row.get("Event-Confidence")),
                 self.actor_for(row.get("Owner")), s(row.get("Owner")),
                 str(date_in) if date_in else None,
                 self.system, self.system))
            lid = self.cur.fetchone()[0]
            self.mark("lead", lid, "lead-registry", ref)
            if s(row.get("Next Action")):
                owner = self.actor_for(row.get("Owner")) or self.system
                na_due, na_raw = dt(row.get("Next Action Date"), "Next Action Date", ref)
                desc = s(row.get("Next Action")) + (f" [{na_raw}]" if na_raw else "")
                self.cur.execute(
                    "insert into next_action (subject_type, subject_id, owner_id, due_on, description, "
                    "created_by) values ('lead',%s,%s,%s,%s,%s) on conflict do nothing",
                    (lid, owner, na_due, desc, self.system))
            lt, lt_raw = dt(row.get("Last Touch"), "Last Touch", ref)
            if lt:
                self.cur.execute(
                    "insert into activity (occurred_at, actor_id, kind, summary, lead_id, source) "
                    "values (%s,%s,'note','last-touch stamp (imported)',%s,'import')",
                    (lt, self.actor_for(row.get("Owner")) or self.system, lid))
            self.event("lead", lid, f"imported {ref} from lead-registry.xlsx")
            n += 1
        report["counts"]["leads"] = n

    def import_clients(self):
        rows = self.sheet_rows("DNA/Clients/client-roster.xlsx", "Clients")
        n = 0
        for row in rows:
            ref = s(row.get("Client ID"))
            if not ref:
                report["skipped"].append(f"roster: row without Client ID ({s(row.get('Name'))})")
                continue
            if self.seen("client-roster", ref):
                continue
            org = self.party(row.get("Practice / Entity"), kind="org", src=f"roster:{ref}") \
                if s(row.get("Practice / Entity")) else None
            loc = s(row.get("Market / Location")) or ""
            city = loc.split(",")[0].strip() or None
            state = loc.split(",")[1].strip() if "," in loc else None
            pid = self.party(row.get("Name"), org_id=org, phone=row.get("Phone"),
                             email=row.get("Email"), city=city, state=state, src=f"roster:{ref}")
            if not pid:
                report["skipped"].append(f"roster: {ref} has no Name")
                continue
            spec = s(row.get("Specialty / Type")) or ""
            vertical = spec.split("/")[0].strip() or None
            subtype = spec.split("/")[1].strip() if "/" in spec else None
            status = self.vocab("client_status", row.get("Status")) or "prospect"
            self.cur.execute(
                "insert into client (roster_ref, party_id, vertical, subtype, status, "
                "acquisition_source, notes_path, owner_id, owner_label, contact_label, deal_type_label, "
                "possible_duplicate_label, notes, specialty_type_label, created_by, updated_by) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (ref, pid, vertical, subtype, status, s(row.get("Referral Source")),
                 s(row.get("Detail File")), self.actor_for(row.get("Owner")),
                 s(row.get("Owner")), s(row.get("Contact")), s(row.get("Deal Type")),
                 s(row.get("Possible Duplicate Of")), s(row.get("Notes")), spec,
                 self.system, self.system))
            cid = self.cur.fetchone()[0]
            self.mark("client", cid, "client-roster", ref)
            if s(row.get("Possible Duplicate Of")):
                report["dup_candidates"].append(
                    f"roster:{ref} carries live flag 'Possible Duplicate Of: "
                    f"{s(row.get('Possible Duplicate Of'))}' — carried into the review queue")
                self.mark("client", cid, "dup_candidate", f"{ref}:{s(row.get('Possible Duplicate Of'))}")
            self.event("client", cid, f"imported {ref} from client-roster.xlsx")
            n += 1
        report["counts"]["clients"] = n

    def import_deals(self):
        path = self.vault / "DNA/Deal Management/panhandle-team-deals.json"
        doc = json.loads(path.read_text())
        n = 0
        for deal in doc.get("deals", []):
            key = s(deal.get("name"))
            if not key:
                report["skipped"].append("deals: unnamed deal in JSON")
                continue
            if self.seen("panhandle-team-deals", key):
                continue
            # client: match roster import by company org name, else create
            company = s(deal.get("company"))
            self.cur.execute(
                "select c.id from client c join party p on p.id=c.party_id "
                "left join party o on o.id=p.org_id "
                "where lower(coalesce(o.name,p.name)) = lower(%s) limit 1", (company or key,))
            r = self.cur.fetchone()
            if r:
                cid = r[0]
            else:
                org = self.party(company, kind="org", src=f"deals:{key}") if company else None
                pid = self.party(deal.get("contact") or company or key, org_id=org,
                                 phone=deal.get("phone"), email=deal.get("email"),
                                 city=deal.get("city"), src=f"deals:{key}")
                status = self.vocab("client_status", deal.get("carr_status") or "active") or "active"
                self.cur.execute(
                    "insert into client (party_id, status, etl_status, owner_label, created_by, "
                    "updated_by) values (%s,%s,%s,%s,%s,%s) returning id",
                    (pid, status, s(deal.get("etl")), s(deal.get("owner")), self.system, self.system))
                cid = self.cur.fetchone()[0]
                report["notes"].append(f"deals:{key}: no roster match for '{company}' — client created "
                                       f"WITHOUT C-ref (mint or merge at review)")
            txn = str(deal.get("txn") or "").strip().lower()
            deal_type = DEAL_TYPE_MAP.get(txn)
            if not deal_type:
                deal_type = "other"
                report["type_fallbacks"].append(f"deals:{key}: txn '{deal.get('txn')}' -> 'other'")
            phase = self.vocab("deal_phase", deal.get("phase")) or "pending"
            carr_raw = s(deal.get("carr"))
            carr_num = None
            if carr_raw:
                m = re.sub(r"[^0-9.]", "", carr_raw)
                carr_num = float(m) if m else None
            self.cur.execute(
                "insert into deal (client_id, name, deal_type, phase, segment, "
                "sf_commission_placeholder, source_row, created_by, updated_by) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (cid, key, deal_type, phase, s(deal.get("seg") or deal.get("lane")),
                 carr_num, json.dumps(deal), self.system, self.system))
            did = self.cur.fetchone()[0]
            self.mark("deal", did, "panhandle-team-deals", key)
            owner_actor = self.actor_for(deal.get("owner"))
            if owner_actor:
                self.cur.execute(
                    "insert into deal_participant (deal_id, actor_id, role, set_by) "
                    "values (%s,%s,'lead',%s)", (did, owner_actor, self.system))
            if s(deal.get("activity")):
                self.cur.execute(
                    "insert into activity (occurred_at, actor_id, kind, summary, deal_id, source) "
                    "values (now(), %s, 'note', %s, %s, 'import')",
                    (self.system, s(deal.get("activity"))[:2000], did))
            if s(deal.get("next_step")) and owner_actor:
                self.cur.execute(
                    "insert into next_action (subject_type, subject_id, owner_id, description, "
                    "created_by) values ('deal',%s,%s,%s,%s) on conflict do nothing",
                    (did, owner_actor, s(deal.get("next_step")), self.system))
            self.event("deal", did, f"imported '{key}' from panhandle-team-deals.json")
            n += 1
        report["counts"]["deals"] = n

    def seed_sequences(self):
        for seq, table, col, pattern in [
                ("ref_lead_seq", "lead", "registry_ref", r"L-(\d+)"),
                ("ref_client_seq", "client", "roster_ref", r"C-(\d+)"),
                ("ref_vendor_seq", "vendor", "vendor_ref", r"-(\d+)$")]:
            self.cur.execute(f"select {col} from {table} where {col} is not null")
            nums = [int(m.group(1)) for (v,) in self.cur.fetchall()
                    for m in [re.search(pattern, v)] if m]
            top = max(nums) if nums else 0
            self.cur.execute("select setval(%s, %s)", (seq, max(top, 1)))
            report["notes"].append(f"{seq} seeded to {max(top, 1)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=VAULT_DEFAULT)
    a = ap.parse_args()
    url = os.environ.get("CARR_IMPORT_DB_URL") or sys.exit("CARR_IMPORT_DB_URL not set")

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        imp = Importer(cur, a.vault)
        imp.import_vendors()
        imp.import_clients()
        imp.import_leads()
        imp.import_deals()
        imp.seed_sequences()
        conn.commit()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(__file__).resolve().parent.parent / "out" / f"import-report-{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Wave 1 import report — {stamp}", "",
             "## Counts", *[f"- {k}: {v}" for k, v in report["counts"].items()], "",
             "## Vocab added (review each)", *[f"- {v}" for v in report["vocab_added"]], "",
             "## Owner labels with no actor mapping", *[f"- {v}" for v in sorted(report["owners_unmapped"])], "",
             "## Duplicate candidates (HUMAN review, nothing merged)", *[f"- {v}" for v in report["dup_candidates"]], "",
             "## Deal-type fallbacks", *[f"- {v}" for v in report["type_fallbacks"]], "",
             "## Skipped rows", *[f"- {v}" for v in report["skipped"]], "",
             "## Notes", *[f"- {v}" for v in report["notes"]], ""]
    out.write_text("\n".join(lines))
    print(f"report -> {out}")
    for k, v in report["counts"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
