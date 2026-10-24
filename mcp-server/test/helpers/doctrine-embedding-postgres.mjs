import { readMigration } from '../migration-source.testhelper.mjs';
import { runtimeErrorDatabase } from './runtime-error-postgres.mjs';

export async function doctrineEmbeddingDatabase() {
  const fixture = await runtimeErrorDatabase();
  const c = fixture.client;
  try {
    await c.query(`
      create extension if not exists pg_trgm;
      alter table doctrine_document add column title text default '',
        add column content_class text default 'reference', add column owner_actor_id uuid,
        add column title_search_vector tsvector generated always as (to_tsvector('english',coalesce(title,''))) stored;
      alter table doctrine_section add column title_search_vector tsvector generated always as (to_tsvector('english',coalesce(title,''))) stored;
      alter table doctrine_revision add column plain_text text default '',
        add column search_vector tsvector generated always as (to_tsvector('english',coalesce(plain_text,''))) stored;
      create table retrieval_ranking_policy(policy_id text, version bigint, formula text, config jsonb, status text,is_default boolean);
      insert into retrieval_ranking_policy values ('coequal-normalized-v1',1,'coequal_normalized','{"concept_enabled":true,"dual_evidence_bonus":0.15}','active',true);
      create table retrieval_phrase(id uuid, concept_id uuid,weight double precision,match_mode text,normalized_phrase text,min_similarity double precision,status text);
      create table retrieval_concept(id uuid,status text);
      create table doctrine_concept_mapping(id uuid,concept_id uuid,section_id uuid,weight double precision,status text);
      create function normalize_retrieval_phrase(text) returns text language sql immutable as $$ select lower(trim($1)) $$;
      drop function search_doctrine_situations(text,uuid,text[],integer,text,boolean);
      delete from doctrine_section where section_key='runtime-failures';
      delete from doctrine_revision where id='10000000-0000-4000-8000-000000000003';
      delete from doctrine_document where slug='runtime-operations';
    `);
    const original = readMigration('0135_situation_retrieval.sql');
    const candidate = original.slice(original.indexOf('create or replace function current_retrievable_doctrine('), original.indexOf('-- The sole production ranker.'));
    await c.query(candidate);
    const baseline = readMigration('0856_qualify_security_definer_dependencies.sql');
    const start = baseline.indexOf('CREATE OR REPLACE FUNCTION public.search_doctrine_situations(');
    const end = baseline.indexOf('$function$;', start) + '$function$;'.length;
    await c.query(baseline.slice(start,end));
    await c.query(`
      grant usage on schema public to carr_reader,carr_writer;
      grant select on doctrine_section,doctrine_revision,doctrine_document to carr_writer;
      grant execute on function current_retrievable_doctrine(uuid,text[]) to carr_writer;
      grant execute on function search_doctrine_situations(text,uuid,text[],integer,text,boolean) to carr_reader,carr_writer;
      alter default privileges revoke execute on functions from public;
    `);
    await c.query(`
      create table ops.scac_reference_monitor_receipt(admission_id uuid, backend_pid integer,
        transaction_id bigint, session_principal name, effect_keys text[]);
      create table ops.scac_reference_monitor_mode_event(event_no bigint, event_digest text,
        previous_event_digest text, mode text, reason_digest text, grant_digest text,
        idempotency_key uuid, production_enforcement_active boolean);
      create table ops.scac_policy_epoch(epoch bigint, registry_version text, epoch_digest text);
      create table ops.scac_mutation_registry_version(registry_version text, registry_digest text);
    `);
    const monitor = readMigration('0467_siep18_atomic_db_monitor_grants.sql');
    for (const name of ['scac_runtime_privilege_bundle', 'scac_reference_monitor_mode', 'scac_reference_monitor_guard']) {
      const start = monitor.indexOf(`create or replace function ops.${name}()`);
      const end = monitor.indexOf('$fn$;', monitor.indexOf('as $fn$', start) + 8) + '$fn$;'.length;
      await c.query(monitor.slice(start, end));
    }
    await c.query(readMigration('0956_doctrine_embeddings.sql'));
    return fixture;
  } catch (error) { await fixture.close(); throw error; }
}
