// Record deal-owned client needs and match only current approved vendor applicability.
import { type Schema, Validator } from "@cfworker/json-schema";
import responseSchema from "../contracts/client-needs.v1.json" with { type: "json" };
import { ToolError } from "./tool-error.ts";
import type { Actor, Db, WithEnvelope, WriteEvent } from "./verb-contract.ts";

const uuid = { type: "string", format: "uuid" };
const text = { type: "string", minLength: 1, maxLength: 2000, pattern: "\\S" };
const fieldsSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    vendor: uuid,
    description: text,
    applicability: text,
    source_ref: text,
    source_revision: { ...text, maxLength: 200 },
    observed_at: { type: "string", format: "date-time" },
    fresh_until: { type: "string", format: "date-time" },
    restriction: { enum: ["allowed", "restricted"] },
    withdrawn: { type: "boolean" },
  },
  required: [
    "vendor",
    "description",
    "applicability",
    "source_ref",
    "source_revision",
    "observed_at",
    "fresh_until",
    "restriction",
    "withdrawn",
  ],
};
const recordSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    idempotency_key: uuid,
    deal: uuid,
    need: uuid,
    base_version: { type: "integer", minimum: 1 },
    fields: fieldsSchema,
  },
  required: ["idempotency_key", "deal", "fields"],
};
const approveSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    idempotency_key: uuid,
    deal: uuid,
    need: uuid,
    base_version: { type: "integer", minimum: 1 },
  },
  required: ["idempotency_key", "deal", "need", "base_version"],
};
function readSchema(subject: "deal" | "vendor") {
  return {
    type: "object",
    additionalProperties: false,
    properties: {
      [subject]: uuid,
      limit: { type: "integer", minimum: 1, maximum: 50 },
      offset: { type: "integer", minimum: 0, maximum: 10000 },
    },
    required: [subject],
  };
}
type Fields = {
  vendor: string;
  description: string;
  applicability: string;
  source_ref: string;
  source_revision: string;
  observed_at: string;
  fresh_until: string;
  restriction: "allowed" | "restricted";
  withdrawn: boolean;
};
type RecordArgs = { idempotency_key: string; deal: string; need?: string; base_version?: number; fields: Fields };
type ApproveArgs = { idempotency_key: string; deal: string; need: string; base_version: number };
type ReadArgs = { deal?: string; vendor?: string; limit?: number; offset?: number };
function parse<T>(schema: object, args: unknown): T {
  if (!new Validator(structuredClone(schema)).validate(args).valid)
    throw new ToolError({ error: "invalid_need_input" });
  return args as T;
}
type PartnerResolver = (actor: Actor) => string | null;
function authorize(actor: Actor, partnerAuthoritySlugForActor: PartnerResolver, approve = false) {
  const partner = partnerAuthoritySlugForActor(actor);
  // Coordinator default pending Joe's answer. Keep approval policy here.
  if (!partner || (approve && partner !== "joe")) throw new ToolError({ error: "AUTHORIZATION_REFUSED" });
}
const projection = `select n.*,d.client_id,c.version as client_version,d.version as deal_version,
 p.name as client_name,d.name as deal_name,v.vendor_ref,
 (exists(select 1 from public.lead l where l.party_id in (p.id,vp.id) and (coalesce(l.suppressed,false) or l.stage='do_not_contact'))
 or exists(select 1 from public.vendor r where r.party_id in (p.id,vp.id) and r.merged_into is null and r.disposition='avoid')
 or exists(select 1 from public.party held where held.id in (p.id,vp.id) and held.contact_state<>'active'
   and (held.contact_state_until is null or held.contact_state_until>=current_date))) as identity_restricted,
 case when n.withdrawn then 'withdrawn'
 when n.observed_at>statement_timestamp() or n.fresh_until<=statement_timestamp() then 'stale'
 when n.approved_revision=n.revision and n.approved_client_id=d.client_id then 'approved'
 when n.version>1 then 'edited_lapsed' else 'draft' end as state
 from public.deal_client_need n join public.deal d on d.id=n.deal_id
 join public.client c on c.id=d.client_id and c.merged_into is null
 join public.party p on p.id=c.party_id and p.merged_into is null and p.deleted_at is null
 join public.vendor v on v.id=n.vendor_id and v.merged_into is null
 join public.party vp on vp.id=v.party_id and vp.merged_into is null and vp.deleted_at is null`;
const jsonProjection = `select to_jsonb(r) as need from (${projection} where n.id=$1) r`;
async function readNeed(c: Db, id: string) {
  const row = (await c.query(jsonProjection, [id])).rows[0];
  if (!row) throw new ToolError({ error: "not_found" });
  return row.need;
}
async function lockContext(c: Db, deal: string, vendor?: string) {
  const d = (
    await c.query(
      `select d.client_id from public.deal d
 join public.client c on c.id=d.client_id and c.merged_into is null
 join public.party p on p.id=c.party_id and p.merged_into is null and p.deleted_at is null
 where d.id=$1 for share of d,c,p`,
      [deal],
    )
  ).rows[0];
  if (!d) throw new ToolError({ error: "not_found" });
  if (
    vendor &&
    !(
      await c.query(
        `select v.id from public.vendor v join public.party p on p.id=v.party_id
 where v.id=$1 and v.merged_into is null and p.merged_into is null and p.deleted_at is null for share of v,p`,
        [vendor],
      )
    ).rows.length
  )
    throw new ToolError({ error: "not_found" });
  return d;
}
async function readNeeds(c: Db, actor: Actor, args: unknown, subject: "deal" | "vendor", resolver: PartnerResolver) {
  authorize(actor, resolver);
  const a = parse<ReadArgs>(readSchema(subject), args),
    id = a[subject],
    limit = a.limit ?? 20,
    offset = a.offset ?? 0;
  const exists =
    subject === "deal"
      ? `select d.id from public.deal d join public.client c on c.id=d.client_id join public.party p on p.id=c.party_id
     where d.id=$1 and c.merged_into is null and p.merged_into is null and p.deleted_at is null`
      : `select v.id from public.vendor v join public.party p on p.id=v.party_id where v.id=$1
     and v.merged_into is null and p.merged_into is null and p.deleted_at is null`;
  if (!(await c.query(exists, [id])).rows.length) throw new ToolError({ error: "not_found" });
  const eligibility =
    subject === "vendor" ? `where state='approved' and restriction='allowed' and not identity_restricted` : "";
  const result = (
    await c.query(
      `with needs as (${projection} where n.${subject}_id=$1),
 eligible as (select * from needs ${eligibility}), page as (
 select * from eligible order by id limit $2 offset $3)
 select statement_timestamp()::text as as_of,
 coalesce((select jsonb_agg(to_jsonb(page) order by id) from page),'[]'::jsonb) as rows,
 (select count(*)::int from eligible) as total,
 exists(select 1 from needs where state in ('draft','edited_lapsed') and restriction='allowed' and not identity_restricted) as missing_approval`,
      [id, limit, offset],
    )
  ).rows[0];
  const response = {
    schema_version: "client-needs.v1",
    subject,
    subject_id: id,
    as_of: result.as_of,
    state: result.rows.length ? "ready" : result.missing_approval ? "missing_approval" : "empty",
    rows: result.rows,
    next_offset: offset + limit < result.total ? offset + limit : null,
  };
  if (!new Validator(structuredClone(responseSchema) as unknown as Schema).validate(response).valid)
    throw new ToolError({ error: "invalid_need_response" });
  return response;
}
export function clientNeedTools({
  withEnvelope,
  writeEvent,
  partnerAuthoritySlugForActor,
}: {
  withEnvelope: WithEnvelope;
  writeEvent: WriteEvent;
  partnerAuthoritySlugForActor: PartnerResolver;
}) {
  return {
    "read-deal-client-needs-v1": {
      write: false,
      authorityOnly: true,
      inputSchema: readSchema("deal"),
      description:
        "Read explicit deal-owned client needs and exact revisions for approval review. Never interprets checklist prose as approval.",
      handler: (c: Db, actor: Actor, args: unknown) => readNeeds(c, actor, args, "deal", partnerAuthoritySlugForActor),
    },
    "read-vendor-client-needs-v1": {
      write: false,
      authorityOnly: true,
      inputSchema: readSchema("vendor"),
      description:
        "Read client-needs.v1 matches for one exact vendor. Only approved current unrestricted needs match; no vertical or geography inference. Live bounded pagination.",
      handler: (c: Db, actor: Actor, args: unknown) =>
        readNeeds(c, actor, args, "vendor", partnerAuthoritySlugForActor),
    },
    "record-deal-client-need-v1": {
      write: true,
      authorityOnly: true,
      inputSchema: recordSchema,
      description:
        "Record, edit or withdraw an explicit sourced client need from its deal. Editing any need content lapses approval. Use the current need base_version.",
      handler: async (c: Db, actor: Actor, args: unknown) => {
        authorize(actor, partnerAuthoritySlugForActor);
        const a = parse<RecordArgs>(recordSchema, args);
        if (
          Boolean(a.need) !== (a.base_version !== undefined) ||
          Date.parse(a.fields.fresh_until) <= Date.parse(a.fields.observed_at)
        )
          throw new ToolError({ error: "invalid_need_input" });
        return withEnvelope(c, actor, "record-deal-client-need-v1", a, async () => {
          await lockContext(c, a.deal, a.fields.vendor);
          const f = a.fields,
            params = [
              a.deal,
              f.vendor,
              f.description,
              f.applicability,
              f.source_ref,
              f.source_revision,
              f.observed_at,
              f.fresh_until,
              f.restriction,
              f.withdrawn,
              actor.id,
            ];
          let id: string;
          if (a.need) {
            const before = (
              await c.query("select version from public.deal_client_need where id=$1 and deal_id=$2 for update", [
                a.need,
                a.deal,
              ])
            ).rows[0];
            if (!before) throw new ToolError({ error: "not_found" });
            if (before.version !== a.base_version)
              throw new ToolError({ error: "version_conflict", current_version: before.version });
            await c.query(
              `update public.deal_client_need set vendor_id=$2,description=$3,applicability=$4,source_ref=$5,
    source_revision=$6,observed_at=$7,fresh_until=$8,restriction=$9,withdrawn=$10,updated_by=$11 where deal_id=$1 and id=$12`,
              [...params, a.need],
            );
            id = a.need;
          } else {
            id = (
              await c.query(
                `insert into public.deal_client_need(deal_id,vendor_id,description,applicability,source_ref,
    source_revision,observed_at,fresh_until,restriction,withdrawn,created_by,updated_by)
    values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11) returning id`,
                params,
              )
            ).rows[0].id;
          }
          const need = await readNeed(c, id);
          await writeEvent(c, actor, "record-deal-client-need-v1", "deal", a.deal, {
            field: "client_need",
            new: { need },
            idempotency_key: a.idempotency_key,
          });
          return { schema_version: "client-needs.v1", need };
        });
      },
    },
    "approve-deal-client-need-v1": {
      write: true,
      authorityOnly: true,
      inputSchema: approveSchema,
      description:
        "Approve the exact displayed client need revision from its deal. Refuses withdrawn, restricted or stale needs; edits lapse approval automatically.",
      handler: async (c: Db, actor: Actor, args: unknown) => {
        authorize(actor, partnerAuthoritySlugForActor, true);
        const a = parse<ApproveArgs>(approveSchema, args);
        return withEnvelope(c, actor, "approve-deal-client-need-v1", a, async () => {
          const deal = await lockContext(c, a.deal);
          const row = (
            await c.query(
              `select version,not withdrawn and restriction='allowed' and observed_at<=statement_timestamp()
   and fresh_until>statement_timestamp() as approvable from public.deal_client_need where id=$1 and deal_id=$2 for update`,
              [a.need, a.deal],
            )
          ).rows[0];
          if (!row) throw new ToolError({ error: "not_found" });
          if (row.version !== a.base_version)
            throw new ToolError({ error: "version_conflict", current_version: row.version });
          if (!row.approvable) throw new ToolError({ error: "need_not_approvable" });
          await c.query(
            `update public.deal_client_need set version=version+1,approved_revision=revision,
   approved_client_id=$2,approved_by=$3,approved_at=clock_timestamp(),updated_by=$3 where id=$1`,
            [a.need, deal.client_id, actor.id],
          );
          const need = await readNeed(c, a.need);
          await writeEvent(c, actor, "approve-deal-client-need-v1", "deal", a.deal, {
            field: "client_need",
            new: { need },
            idempotency_key: a.idempotency_key,
          });
          return { schema_version: "client-needs.v1", need };
        });
      },
    },
  };
}
