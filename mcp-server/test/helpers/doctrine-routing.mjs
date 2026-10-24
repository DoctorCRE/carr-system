import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import pg from 'pg';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './disposable-postgres.mjs';

export async function doctrineRoutingFixture() {
  const bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/18/bin', '/usr/lib/postgresql/17/bin']
    .find(candidate => existsSync(path.join(candidate, 'initdb')));
  if (!bin) throw new Error('PostgreSQL server binaries are required for routing acceptance');
  const release = await acquirePostgresFixtureGroup();
  let fixture, client;
  const close = async () => {
    try { await client?.end(); } finally {
      try { await fixture?.close(); } finally { await release(); }
    }
  };
  try {
    fixture = await acquireDisposablePostgres({ prefix: 'doctrine-routing-', pgCtl: path.join(bin, 'pg_ctl') });
    const data = path.join(fixture.root, 'data');
    await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
      '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    client = new pg.Client({ host: fixture.root, user: 'fixture', database: 'postgres' });
    await client.connect();
    await client.query(`
      create extension pg_trgm;
      create table doctrine_document(id uuid primary key default gen_random_uuid(), slug text unique, title text,
        content_class text, visibility text default 'shared', owner_actor_id uuid,
        title_search_vector tsvector generated always as
          (to_tsvector('english', coalesce(title, '') || ' ' || replace(slug, '-', ' '))) stored);
      create table doctrine_section(id uuid primary key, document_id uuid, section_key text, title text,
        status text default 'active', current_revision_id uuid,
        title_search_vector tsvector generated always as
          (to_tsvector('english', coalesce(title, '') || ' ' || replace(section_key, '-', ' '))) stored);
      create table doctrine_revision(id uuid primary key default gen_random_uuid(), section_id uuid, plain_text text,
        search_vector tsvector generated always as (to_tsvector('english', plain_text)) stored);
      create table retrieval_ranking_policy(policy_id text, version bigint, status text, is_default boolean, formula text, config jsonb);
      insert into retrieval_ranking_policy values ('coequal-normalized-v1',1,'active',true,'coequal_normalized',
        '{"concept_enabled":true,"dual_evidence_bonus":0.15}');
      create table retrieval_phrase(id uuid, concept_id uuid, weight numeric, normalized_phrase text,
        match_mode text, min_similarity numeric, status text);
      create table retrieval_concept(id uuid, status text);
      create table doctrine_concept_mapping(id uuid, concept_id uuid, section_id uuid, weight numeric, status text);
    `);
    const original = readFileSync(new URL('../../../migrations/0135_situation_retrieval.sql', import.meta.url), 'utf8');
    for (const name of ['normalize_retrieval_phrase', 'current_retrievable_doctrine']) {
      const start = original.indexOf(`create or replace function ${name}(`);
      await client.query(original.slice(start, original.indexOf('$$;', start) + 3));
    }
    const qualified = readFileSync(new URL('../../../migrations/0856_qualify_security_definer_dependencies.sql', import.meta.url), 'utf8');
    const start = qualified.indexOf('CREATE OR REPLACE FUNCTION public.search_doctrine_situations(');
    await client.query(qualified.slice(start, qualified.indexOf('$function$;', start) + 11));
    return {
      client, close,
      async insert(section, document = {}) {
        const doc = await client.query(`insert into doctrine_document(slug,title,content_class,visibility,owner_actor_id)
          values ($1,$2,$3,$4,$5) on conflict(slug) do update set slug=excluded.slug returning id`,
        [section.doc_slug, document.title || section.doc_slug.replaceAll('-', ' '), section.content_class || 'sop',
          section.visibility || 'shared', document.owner_actor_id || null]);
        const revision = await client.query('insert into doctrine_revision(section_id,plain_text) values ($1,$2) returning id',
          [section.id, typeof section.body === 'string' ? section.body : section.body.text]);
        await client.query(`insert into doctrine_section(id,document_id,section_key,title,status,current_revision_id)
          values ($1,$2,$3,$4,$5,$6)`, [section.id,doc.rows[0].id,section.section_key,section.title,
          section.status || 'active',revision.rows[0].id]);
      },
      async search(q, { actor = null, classes = null, fallback = false } = {}) {
        return (await client.query('select * from search_doctrine_situations($1,$2,$3,10,null,$4)',
          [q,actor,classes,fallback])).rows;
      },
    };
  } catch (error) { await close(); throw error; }
}
