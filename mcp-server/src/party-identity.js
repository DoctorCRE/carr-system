// CORRECT-PARTY-IDENTITY (Joe, 2026-10-08, about contact enrichment: "dont
// propose corrections, just make them"; rule 578fdd91). update-party-contact
// writes contact facts and refuses identity on purpose. This is the identity
// door: a party's name, its firm (org) and its state, from a verified finding.
//
// Three properties carry the rule's trade-off (an occasional wrong edit that is
// cheap to undo, over a pile of proposals nobody applies):
//   - confirmed high/medium identity evidence and a re-verifiable source are
//     required before entering the envelope; independence is checked against
//     the resolved party before any write;
//     every changed field records its evidence and prior value for reversal;
//   - base_version guards the party exactly as update-party-contact does;
//   - an org change follows rule 8cddc6ad. A shared org row is never renamed,
//     because renaming it would re-label every other record on it. "Shared" is
//     counted by the database across EVERY foreign key into party
//     (party_reference_counts, migration 0852), not only party.org_id: a deal
//     participant, a client or vendor row, or a party link on the org row makes
//     it shared too. Only the target is re-pointed, and the other people on the
//     old org are read back afterwards; if any moved, the call refuses and the
//     transaction rolls back.
//
// Organisation identity is the database's org_identity_key(), the same key the
// unique index party_org_identity_uniq enforces; minting goes through
// org_party_id(), the shared find-or-create (rule a8c55a47: one job, one code
// path). Neither is re-implemented here.
import { ToolError } from "./tool-error.js";

export const PARTY_IDENTITY_FIELDS = Object.freeze(["name", "org", "state"]);
const CORROBORATING_FIELDS = Object.freeze(["firm", "email_domain", "city", "phone", "address", "npi"]);
const UNCONFIRMED = /\b(?:unconfirmed|unverified|confirm|possible match|surname[- ]only|not (?:yet )?confirmed)\b/i;

// Evidence values (addresses, cities) must be one value, not a list. Only a
// separator or a lowercase " or " counts, so a capital OR state code
// ("Portland OR 97209") and a slash in a suite number are not misread.
const ALTERNATIVES = /[;\r\n]|\s+or\s+(?=\S)/;
// The corrected name or org itself stays strict: "Smith OR Jones" or
// "Dr Lee / Dr Kim" is two candidates, never one confirmed value.
const FIELD_ALTERNATIVES = /[;\r\n]|\s(?:or|\/)\s/i;
// A consumer mail domain proves nothing about who someone works for.
const FREE_MAIL_DOMAINS = new Set(["gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com",
  "icloud.com", "me.com", "mac.com", "aol.com", "hotmail.com", "live.com", "msn.com", "protonmail.com",
  "proton.me", "bellsouth.net", "att.net", "comcast.net", "sbcglobal.net", "cox.net", "charter.net"]);
const UUID = /\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/i;
// "confirm" inside a URL path is not an unconfirmed marker.
const withoutUrls = text => text.replace(/https?:\/\/\S+/gi, " ");

const NAME_IDENTIFIERS = new Set(["email_domain", "phone", "address", "npi"]);
const normalizedText = v => typeof v === "string" ? v.trim().replace(/\s+/g, " ").toLowerCase() : "";

function phoneIdentifier(value) {
  if (typeof value !== "string" || !/^[+()\d .-]+$/.test(value)) return "";
  const digits = value.replace(/\D/g, "");
  return /^\d{10}$/.test(digits) ? digits : /^1\d{10}$/.test(digits) ? digits.slice(1) : "";
}

function storedIdentifierMatches(field, value, before) {
  switch (field) {
    case "email_domain": {
      const domain = normalizedText(value);
      const stored = typeof before.email === "string" ? before.email.trim().split("@") : [];
      return /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$/.test(domain)
        && stored.length === 2 && Boolean(stored[0]) && normalizedText(stored[1]) === domain;
    }
    case "phone": {
      const phone = phoneIdentifier(value);
      return Boolean(phone) && [before.phone, before.cell].some(v => phoneIdentifier(v) === phone);
    }
    case "address":
      return Boolean(normalizedText(before.street_address))
        && normalizedText(before.street_address) === normalizedText(value);
    case "npi":
      return /^\d{10}$/.test(value) && before.npi === value;
    default:
      return false;
  }
}

// Name fields require a typed identifier, then a match to the resolved record.
// No name, alias, firm or city string can substitute for that match.
function identityEvidence(args, before) {
  const evidence = args.evidence;
  const value = typeof evidence?.corroborating_value === "string" ? evidence.corroborating_value.trim() : "";
  const changesName = args.fields?.name !== undefined || args.fields?.org !== undefined;
  const city = evidence?.city;
  if (!evidence || evidence.confirmed !== true || !["high", "medium"].includes(evidence.confidence)
      || !CORROBORATING_FIELDS.includes(evidence.corroborating_field) || !value || value.length > 200
      || UNCONFIRMED.test(value) || ALTERNATIVES.test(value) || isPlaceholder(value)
      || (evidence.corroborating_field === "email_domain" && /(?:^|\.)carr\.us$/i.test(value))
      || (evidence.corroborating_field === "email_domain"
        && FREE_MAIL_DOMAINS.has(value.toLowerCase().replace(/^@/, "")))
      || (changesName && (!NAME_IDENTIFIERS.has(evidence.corroborating_field)
        || (before && !storedIdentifierMatches(evidence.corroborating_field, value, before))))
      || (city !== undefined && (typeof city !== "string" || !city.trim() || city.length > 200
        || UNCONFIRMED.test(city) || ALTERNATIVES.test(city)
        || (changesName && before && normalizedText(city) !== normalizedText(before.city))))
      || (!changesName && [args.fields?.state, before?.state].some(v => normalizedText(v) === normalizedText(value))))
    throw new ToolError({ error: "identity_evidence_required",
      hint: "identity must be confirmed:true at high or medium confidence; name and firm corrections require email_domain, phone, address or npi matching the stored party independently. City is supplementary only; names, aliases, firms, orgs and titles never corroborate a name correction" });
  return { confirmed: true, confidence: evidence.confidence,
    corroborating_field: evidence.corroborating_field, corroborating_value: value,
    ...(city === undefined ? {} : { city: city.trim() }) };
}

const US_STATES = new Set(("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN " +
  "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU").split(" "));

// Rule 54e2bcb9: a CARR agent's own line or a carr.us address standing in for a
// value nobody had. The same guard update-party-contact applies to contact fields.
function isPlaceholder(value) {
  return /@carr\.us\b/i.test(value) || value.replace(/\D/g, "").includes("2056436555");
}

export function normalizeIdentityFields(fields) {
  const keys = Object.keys(fields || {}).filter(k => PARTY_IDENTITY_FIELDS.includes(k));
  if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed: PARTY_IDENTITY_FIELDS,
    hint: "identity fields only (name, org, state); contact facts go through update-party-contact" });
  const clean = {};
  for (const k of keys) {
    if (typeof fields[k] === "string" && (UNCONFIRMED.test(fields[k]) || FIELD_ALTERNATIVES.test(fields[k])))
      throw new ToolError({ error: "unconfirmed_identity", field: k,
        hint: "apply only one clean, confirmed value; values marked unconfirmed or confirm, or listing alternatives, cannot be written" });
    const value = typeof fields[k] === "string" ? fields[k].trim().replace(/\s+/g, " ") : "";
    if (k === "state") {
      const code = value.toUpperCase();
      if (!US_STATES.has(code)) throw new ToolError({ error: "invalid_state", got: fields[k],
        hint: "a two-letter US state code such as FL or AL" });
      clean.state = code;
      continue;
    }
    if (!value || value.length > 200) throw new ToolError({ error: `invalid_${k}`, got: fields[k] ?? null,
      hint: `${k} must be a non-empty name of at most 200 characters` });
    if (isPlaceholder(value)) throw new ToolError({ error: "placeholder_value", field: k,
      hint: "a CARR agent's own number or a carr.us address is a placeholder, never data" });
    clean[k] = value;
  }
  return clean;
}

// Rule 8cddc6ad as a pure decision. Inputs are already resolved by the database:
// `existing` is every OTHER live org whose org_identity_key equals the new
// name's, `sameIdentity` says the new name is only a re-spelling of the current
// org (same key), and `shared` says something other than the target refers to
// the current org row. Order matters:
//   1. the firm is already spelled right     -> unchanged
//   2. a live org already is that firm       -> re-point to it (never mint a twin)
//   3. a re-spelling of the same firm        -> rename in place when unshared;
//                                               refuse when shared (a twin cannot
//                                               be minted under the identity index,
//                                               and a rename would re-label others)
//   4. nothing else refers to the old row    -> rename the row in place
//   5. the row is shared, or there is none   -> mint the org, re-point the target
export function planOrgCorrection({ currentOrg, newName, existing, sameIdentity, shared }) {
  if (currentOrg && currentOrg.name === newName) return { mode: "unchanged", org_id: currentOrg.id };
  if (existing.length > 1) throw new ToolError({ error: "org_ambiguous", candidates: existing,
    hint: "several live orgs carry that identity; merge them first, then correct this party" });
  if (existing.length === 1) return { mode: "repoint_existing", org_id: existing[0].id };
  if (currentOrg && sameIdentity) {
    if (shared) throw new ToolError({ error: "shared_org_respelling", org: currentOrg, wanted: newName,
      hint: "the new name is a re-spelling of a shared org row; renaming it re-labels every record on it (rule 8cddc6ad) and a second row with the same identity cannot exist. Correct the org row itself only after confirming the spelling holds for all of them." });
    return { mode: "rename_in_place", org_id: currentOrg.id };
  }
  if (currentOrg && !shared) return { mode: "rename_in_place", org_id: currentOrg.id };
  return { mode: "mint_and_repoint" };
}

const peopleOnOrg = async (c, orgId, exceptId) => (await c.query(
  `select id, name from party where org_id=$1 and id is distinct from $2
      and merged_into is null and deleted_at is null order by name`, [orgId, exceptId])).rows;

const referenceCounts = async (c, partyId, exceptId) => (await c.query(
  "select source, n from party_reference_counts($1,$2)", [partyId, exceptId]))
  .rows.map(r => ({ source: r.source, n: Number(r.n) }));

export function partyIdentityTools({ withEnvelope, writeEvent, versionGuard, resolvePartyForWrite }) {
  return {
    "correct-party-identity": {
      write: true,
      description: "Correct a party's IDENTITY from a verified finding: name spelling, firm (org) and state. The companion to update-party-contact, which handles contact facts only. Per rule 578fdd91 enrichment applies its corrections rather than parking them as proposals, but only when identity is confirmed (high or medium confidence with a second corroborating field). Name and firm changes require an email domain, phone (office or cell), street address or NPI that matches the stored party independently. Firm, org, title, alias and other name-like evidence never count; city is supplementary only. Each corrected value must be one clean value from a re-verifiable source. source is REQUIRED, and every changed field writes an event with its prior value, so each correction can be undone (v_party_identity_correction lists them). base_version is the PARTY's version from a fresh read. ORG CHANGES FOLLOW RULE 8cddc6ad: a shared org row is never renamed. Sharing is counted across every foreign key into the org row, not only other people on it. If a live org already has the corrected identity the party is re-pointed to it; if nothing but this party refers to the current org row, that row is renamed in place; otherwise a new org is minted and only this party is re-pointed. The other people on the old org are read back and returned as `untouched`, and an old org left with no references is reported as `old_org_left_empty`. An ORG party's own name (fields.name) is renamed only when at most one record refers to it (its own role row); people on it, or more references of any kind, refuse and are named. Placeholder guard: a CARR agent's own number or a carr.us address is refused.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        party: { type: "string", description: "P-#### ref, a role ref (V-/C-/L-/T-), or a name" },
        base_version: { type: "integer" },
        fields: { type: "object", additionalProperties: false, properties: {
          name: { type: "string", description: "the party's corrected name" },
          org: { type: "string", description: "the corrected firm name, for a person" },
          state: { type: "string", description: "two-letter US state code" } } },
        source: { type: "string", description: "re-verifiable source: a URL, a registry and identifier, or a record-finding UUID; never an unconfirmed match" },
        evidence: { type: "object", additionalProperties: false, properties: {
          confirmed: { type: "boolean", const: true },
          confidence: { type: "string", enum: ["high", "medium"] },
          corroborating_field: { type: "string", enum: CORROBORATING_FIELDS },
          corroborating_value: { type: "string", minLength: 1, maxLength: 200,
            description: "For name or firm corrections: email domain, phone, street address or NPI matching the stored party. Address matches the latest unexpired source-backed address finding with value.street_address. City and firm alone are allowed only for state-only corrections" },
          city: { type: "string", minLength: 1, maxLength: 200,
            description: "Optional supplementary city; for a name or firm correction it must match the stored city and accompany a matching non-name identifier" } },
          required: ["confirmed", "confidence", "corroborating_field", "corroborating_value"] } },
        required: ["idempotency_key", "party", "base_version", "fields", "source", "evidence"] },
      handler: async (c, actor, args) => {
        const source = typeof args.source === "string" ? args.source.trim() : "";
        if (!source) throw new ToolError({ error: "missing_source",
          hint: "an identity correction without provenance cannot be checked or undone; say where it came from" });
        if (UNCONFIRMED.test(withoutUrls(source))) throw new ToolError({ error: "unconfirmed_identity",
          hint: "this source marks identity unconfirmed; record a possible match without applying a correction" });
        const evidence = identityEvidence(args);
        if (!/https?:\/\/[^\s/]+/i.test(source)
            && !/\b(?:registry|nppes|npi|sunbiz)\b.*\b[a-z]*\d[a-z\d-]*\b/i.test(source)
            && !UUID.test(source))
          throw new ToolError({ error: "source_not_a_locator",
            hint: "supply a re-verifiable URL, registry and identifier, or record-finding UUID" });
        const fields = normalizeIdentityFields(args.fields);
        return withEnvelope(c, actor, "correct-party-identity", args, async () => {
          // org_party_id returns only an ID, including on concurrent reuse.
          // Stabilise the lookup against every party writer. EXCLUSIVE also
          // waits for version guards' ROW SHARE locks, so a row-locked writer
          // cannot wait for our table lock while we wait for its row. Plain
          // reads continue; advisory locks do not cover other creation paths.
          if (fields.org !== undefined) await c.query("lock table party in exclusive mode");
          const { partyId, hopped } = await resolvePartyForWrite(c, args.party);
          await versionGuard(c, "party", partyId, args.base_version);
          const before = (await c.query(
            `select p.kind, p.name, p.state, p.org_id, o.name as org_name,
                    p.email, p.phone, p.cell, p.npi, p.city,
                    (select f.value->>'street_address' from record_flag f
                       where f.subject_type='party' and f.subject_id=p.id and f.kind='address'
                         and f.value->>'found'='true' and f.value->>'epistemic_status'='source_backed'
                         and (f.expires_on is null or f.expires_on>=current_date)
                       order by f.observed_at desc, f.id desc limit 1) as street_address
               from party p left join party o on o.id=p.org_id where p.id=$1`, [partyId])).rows[0];
          if (!before) throw new ToolError({ error: "not_found", table: "party", id: partyId });
          // Check stored identifiers before any correction or envelope receipt.
          identityEvidence(args, before);
          if (fields.org !== undefined && before.kind !== "person")
            throw new ToolError({ error: "org_on_org_party",
              hint: "an org party has no firm; correct its own name with fields.name" });

          // old/new carry the field's value plus any context keys; the view reads
          // old_value->field and new_value->field, and new_value->>'mode'.
          const event = (subjectId, field, oldValue, newValue, { oldExtra = {}, newExtra = {} } = {}) =>
            writeEvent(c, actor, "correct-party-identity", "party", subjectId, {
              field, old: { [field]: oldValue, ...oldExtra }, new: { [field]: newValue, ...newExtra, source, evidence },
              agent_rationale: `source: ${source}`, idempotency_key: args.idempotency_key });
          const lockIdentity = key => c.query("select pg_advisory_xact_lock(hashtext($1))", [`org_identity:${key}`]);
          const updated = [];

          // An org party's own name: the rename IS the correction, so the only
          // question is what else it would re-label. Rule 8cddc6ad read
          // literally: one reference (the role record the correction is about,
          // such as its own vendor row) renames in place; people on it, or more
          // than one reference of any kind, refuses and names them.
          if (before.kind === "org" && fields.name !== undefined && fields.name !== before.name) {
            await c.query("select id from party where id=$1 for update", [partyId]);
            const people = await peopleOnOrg(c, partyId, partyId);
            const references = await referenceCounts(c, partyId, partyId);
            const total = references.reduce((sum, r) => sum + r.n, 0);
            if (people.length || total > 1) throw new ToolError({ error: "shared_org_rename",
              attached: people, references,
              hint: "renaming this org re-labels every record on it (rule 8cddc6ad); correct each person's org instead, or merge duplicates first" });
            const k = (await c.query("select org_identity_key($1) as new_key, org_identity_key($2) as cur_key",
              [fields.name, before.name])).rows[0];
            if (k.new_key && k.new_key !== k.cur_key) {
              await lockIdentity(k.new_key);
              const taken = (await c.query(
                `select id, name from party where kind='org' and merged_into is null and deleted_at is null
                    and org_identity_key(name)=$1 and id<>$2`, [k.new_key, partyId])).rows;
              if (taken.length) throw new ToolError({ error: "org_name_taken", existing: taken,
                hint: "another live org already has this identity; this is a duplicate to merge, not a rename" });
            }
          }

          for (const k of ["name", "state"]) {
            if (fields[k] === undefined || fields[k] === before[k]) continue;
            await c.query(`update party set ${k}=$1, updated_by=$2 where id=$3`, [fields[k], actor.id, partyId]);
            await event(partyId, k, before[k], fields[k]);
            updated.push(k);
          }

          let org;
          if (fields.org !== undefined) {
            const currentOrg = before.org_id ? { id: before.org_id, name: before.org_name } : null;
            // Lock the org row BEFORE counting: attaching anything to it by
            // foreign key takes a FOR KEY SHARE lock on this row, so nothing can
            // join between the count and a rename in place.
            if (currentOrg) await c.query("select id from party where id=$1 for update", [currentOrg.id]);
            const k = (await c.query("select org_identity_key($1) as new_key, org_identity_key($2) as cur_key",
              [fields.org, currentOrg?.name ?? null])).rows[0];
            if (!k.new_key) throw new ToolError({ error: "invalid_org", got: fields.org,
              hint: "that string names no organisation (a placeholder such as TBD or unknown)" });
            const sameIdentity = Boolean(currentOrg) && k.new_key === k.cur_key;
            // Serialise concurrent corrections toward the same firm, so two calls
            // cannot both decide to mint it.
            await lockIdentity(k.new_key);
            const existing = sameIdentity ? [] : (await c.query(
              `select id, name from party where kind='org' and merged_into is null and deleted_at is null
                  and org_identity_key(name)=$1 and id is distinct from $2 for update`,
              [k.new_key, currentOrg?.id ?? null])).rows;
            const references = currentOrg ? await referenceCounts(c, currentOrg.id, partyId) : [];
            const others = currentOrg ? await peopleOnOrg(c, currentOrg.id, partyId) : [];
            const plan = planOrgCorrection({ currentOrg, newName: fields.org, existing, sameIdentity,
              shared: references.length > 0 });
            org = { mode: plan.mode, from: currentOrg, references_before: references };

            if (plan.mode === "rename_in_place") {
              await c.query("update party set name=$1, updated_by=$2 where id=$3",
                [fields.org, actor.id, plan.org_id]);
              await event(plan.org_id, "name", currentOrg.name, fields.org,
                { newExtra: { mode: plan.mode, renamed_for: partyId } });
              org.org_id = plan.org_id;
            } else if (plan.mode === "repoint_existing" || plan.mode === "mint_and_repoint") {
              let orgId = plan.org_id;
              const orgName = plan.mode === "repoint_existing" ? existing[0].name : fields.org;
              if (!orgId) {
                orgId = (await c.query("select org_party_id($1,$2) as id", [fields.org, actor.id])).rows[0].id;
                await event(orgId, "name", null, fields.org, { newExtra: { mode: plan.mode, minted_for: partyId } });
              }
              await c.query("update party set org_id=$1, updated_by=$2 where id=$3", [orgId, actor.id, partyId]);
              await event(partyId, "org_id", currentOrg?.id ?? null, orgId, {
                oldExtra: { org_name: currentOrg?.name ?? null },
                newExtra: { org_name: orgName, mode: plan.mode } });
              org.org_id = orgId;
              // Rule 8cddc6ad step 4: name the untouched people and prove they
              // still read what they read before.
              if (others.length) {
                const after = new Map((await c.query(
                  "select id, org_id from party where id = any($1::uuid[])",
                  [others.map(p => p.id)])).rows.map(r => [r.id, r.org_id]));
                const orgName = (await c.query("select name from party where id=$1", [currentOrg.id])).rows[0]?.name;
                const moved = others.filter(p => after.get(p.id) !== currentOrg.id);
                if (moved.length || orgName !== currentOrg.name)
                  throw new ToolError({ error: "untouched_party_moved", moved, org_name_now: orgName ?? null });
                org.untouched = others.map(p => ({ id: p.id, name: p.name, org_id: currentOrg.id }));
              }
              if (currentOrg && !(await referenceCounts(c, currentOrg.id, null)).length)
                org.old_org_left_empty = currentOrg;
            } else {
              org.org_id = plan.org_id;
            }
            if (plan.mode !== "unchanged") updated.push("org");
          }
          return { ok: true, party_id: partyId, updated, ...(org ? { org } : {}),
            hopped_to_survivor: hopped || undefined };
        });
      },
    },
  };
}
