import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './postgres-database.mjs';

export async function runtimeErrorDatabase() {
  const { client, close } = await postgresDatabase('runtime-error-');
  try {
    await client.query(`
      create schema ops;
      create role carr_reader;
      create role carr_writer;
      create table ops.service(id uuid default gen_random_uuid(), key text, retired_at timestamptz);
      insert into ops.service(key) values ('carr-mcp');
      create table ops.incident (
        id uuid primary key default gen_random_uuid(), ref text unique, correlation_id uuid,
        title text, severity text, state text, environment text, owner_actor text,
        next_action text, business_impact text, detected_source text, detected_at timestamptz,
        source_kind text, source_ref text, signature text, observed_at timestamptz,
        expires_at timestamptz, recovery_evidence_ref text, monitoring_until timestamptz,
        resolved_at timestamptz, reviewed_at timestamptz, last_seen_at timestamptz,
        root_cause text, followup_disposition text, duplicate_of_id uuid);
      create unique index incident_one_open_per_signature on ops.incident(signature)
        where state not in ('resolved','reviewed');
      create table ops.incident_fact(incident_id uuid, text text, source_ref text, recorded_at timestamptz default now());
      create table ops.incident_hypothesis(incident_id uuid, text text, confidence text, settled_as text, recorded_at timestamptz);
      create table ops.incident_service(incident_id uuid, service_id uuid);
      alter table ops.service add column name text, add column criticality text;
      create table ops.incident_link(incident_id uuid, kind text, ref text, note text, unique(incident_id,kind,ref));
      create table ops.run(id uuid, correlation_id uuid);
      create table ops.deployment(id uuid, correlation_id uuid);
      create view ops.v_trace as select null::uuid correlation_id, null::text kind,
        null::text ref, null::text state, null::text environment, null::text service_key,
        null::text failure_class, null::text detail, null::text source_kind,
        null::text source_ref, null::text freshness_state, null::timestamptz occurred_at where false;
      create table board_snapshot(organization_tenant_id text, sponsoring_human_slug text, board_id text, snapshot_json jsonb);
      create table code_subject(id uuid default gen_random_uuid(), repo text, commit_sha text, created_by uuid);
      create unique index code_subject_identity_uniq on code_subject(repo,coalesce(commit_sha,''));
      create table record_flag(id uuid default gen_random_uuid(), subject_type text, subject_id uuid,
        kind text, value jsonb, source text, observed_at timestamptz, expires_on date, created_by uuid);
      create view v_code_finding as select * from record_flag;
      create table tool_call(idempotency_key text primary key, verb text, actor_id uuid,
        request_hash text, response jsonb, via text, client_id text, organization_tenant_id text,
        sponsoring_human_slug text, personal_scope text, authorization_class text, correlation_id uuid);
      create table event(occurred_at timestamptz, recorded_at timestamptz, actor_id uuid,
        verb text, subject_type text, subject_id uuid, field text, old_value jsonb, new_value jsonb,
        cause text, human_quote text, agent_rationale text, idempotency_key text, via text,
        client_id text, organization_tenant_id text, sponsoring_human_slug text, personal_scope text,
        authorization_class text, correlation_id uuid);
      create table doctrine_document(id uuid primary key, slug text, visibility text);
      create table doctrine_section(id uuid primary key, document_id uuid, section_key text,
        title text, status text, current_revision_id uuid);
      create table doctrine_revision(id uuid primary key, section_id uuid);
      insert into doctrine_document values ('10000000-0000-4000-8000-000000000001','runtime-operations','shared');
      insert into doctrine_section values ('10000000-0000-4000-8000-000000000002',
        '10000000-0000-4000-8000-000000000001','runtime-failures','Runtime failures','active',
        '10000000-0000-4000-8000-000000000003');
      insert into doctrine_revision values ('10000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000002');
      create function retrieval_visibility_actor_id(text) returns uuid language sql as
        $$ select '10000000-0000-4000-8000-000000000004'::uuid $$;
      create function search_doctrine_situations(text,uuid,text[],integer,text,boolean)
        returns table(section_id uuid,doc_slug text,section_key text) language sql as
        $$ select s.id,d.slug,s.section_key from doctrine_section s join doctrine_document d on d.id=s.document_id $$;
      create table ops.work_request(id uuid primary key default gen_random_uuid(), ref text unique,
        state text, title text, desired_outcome text, acceptance_criteria jsonb, origin_ref text,
        requester_actor text, program_key text, program_ordinal integer, version integer default 1);
    `);
    const capture = await readFile(new URL('../../../migrations/0174_program6_sourced_work_request_capture.sql', import.meta.url), 'utf8');
    await client.query(capture);
    return { client, close };
  } catch (error) { await close(); throw error; }
}
