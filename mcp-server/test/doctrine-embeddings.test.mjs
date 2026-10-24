import test from 'node:test';
import assert from 'node:assert/strict';
import { searchDoctrineSituations } from '../src/situation-retrieval.js';

const actor = { slug: 'codex', human: false, via: 'agent-token' };

test('embedding failure preserves lexical hits and reports unavailable semantic retrieval', async () => {
  const hit = { section_id: 'lexical-hit', section_key: 'procedure', final_score: 1 };
  const calls = [];
  const client = {
    ai: { run: async () => { throw new Error('provider unavailable'); } },
    query: async (sql, params) => { calls.push({ sql, params }); return { rows: [hit] }; },
  };
  const result = await searchDoctrineSituations(client, actor, { q: 'tenant tour', limit: 10 });
  assert.deepEqual(result.hits, [hit]);
  assert.equal(result.semantic.status, 'unavailable');
  assert.equal(result.semantic.reason, 'embedding_failed');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params.length, 6, 'failure retains the existing lexical call');
});

const unit = (x = 1, y = 0) => [x, y, ...Array(382).fill(0)];
const fixtureIds = Array.from({ length: 30 }, (_, i) => `20000000-0000-4000-8000-${String(i+1).padStart(12,'0')}`);

async function seedSection(c, n, { body = 'sundial instructions', visibility = 'shared', status = 'active', contentClass = 'reference' } = {}) {
  const [doc, section, revision] = fixtureIds.slice(n*3,n*3+3);
  await c.query(`insert into doctrine_document(id,slug,visibility,content_class,owner_actor_id)
    values ($1,$2,$3,$4,'10000000-0000-4000-8000-000000000004')`, [doc,`fixture-${n}`,visibility,contentClass]);
  await c.query(`insert into doctrine_revision(id,section_id,plain_text) values ($1,$2,$3)`, [revision,section,body]);
  await c.query(`insert into doctrine_section(id,document_id,section_key,title,status,current_revision_id)
    values ($1,$2,$3,'',$4,$5)`, [section,doc,`fixture-${n}`,status,revision]);
  return { doc, section, revision };
}

test('real ranker retrieves a meaning-only section with deterministic fake embeddings', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const target = await seedSection(fixture.client,0);
    const ai = { run: async () => ({ data: [unit()] }) };
    const c = { query: fixture.client.query.bind(fixture.client), ai };
    const before = await searchDoctrineSituations({ query:c.query },actor,{ q:'tenant tour',limit:10 });
    assert.equal(before.hits.length,0);
    await c.query(`insert into doctrine_embedding(revision_id,model,embedding) values ($1,$2,$3::vector)`,
        [target.revision,'@cf/baai/bge-small-en-v1.5:cls:chunks-v1',JSON.stringify(unit())]);
    const after = await searchDoctrineSituations(c,actor,{ q:'tenant tour',limit:10 });
    assert.equal(after.hits[0]?.section_id,target.section);
    assert.equal(after.semantic.status,'ready');
    assert.equal(after.hits[0].provenance.semantic_similarity,1);
  } finally { await fixture.close(); }
});

test('both evidence lanes blend while personal, retired, class and stale revision filters remain shared', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const c = { query:fixture.client.query.bind(fixture.client), ai:{run:async()=>({data:[unit()]})} };
    const lexical = await seedSection(c,0,{ body:'tenant tour tenant tour tenant tour' });
    const dual = await seedSection(c,1,{ body:'tenant tour guide' });
    const semantic = await seedSection(c,2);
    const hidden = await seedSection(c,3,{ visibility:'personal',body:'tenant tour' });
    const retired = await seedSection(c,4,{ status:'retired',body:'tenant tour' });
    const otherClass = await seedSection(c,5,{ contentClass:'sop',body:'tenant tour' });
    const stale = await seedSection(c,6);
    const store = async (revision,vector) => c.query(`insert into doctrine_embedding(revision_id,model,embedding) values($1,$2,$3::vector)`,
      [revision,'@cf/baai/bge-small-en-v1.5:cls:chunks-v1',JSON.stringify(vector)]);
    await store(lexical.revision,unit(0,1));
    await store(dual.revision,unit(.8,.6));
    for(const target of [semantic,hidden,retired,otherClass,stale]) await store(target.revision,unit());
    const replacement = fixtureIds[29];
    await c.query('insert into doctrine_revision(id,section_id,plain_text) values($1,$2,$3)',[replacement,stale.section,'sundial instructions']);
    await c.query('update doctrine_section set current_revision_id=$2 where id=$1',[stale.section,replacement]);
    const out=await searchDoctrineSituations(c,actor,{q:'tenant tour',limit:10,content_classes:['reference']});
    assert.equal(out.hits[0].section_id,dual.section,'agreement beats either single evidence lane');
    assert.ok(out.hits.some(hit=>hit.section_id===lexical.section));
    assert.ok(out.hits.some(hit=>hit.section_id===semantic.section));
    for(const excluded of [hidden,retired,otherClass,stale])
      assert.equal(out.hits.some(hit=>hit.section_id===excluded.section),false);
    assert.equal(out.semantic.status,'partial');
    assert.equal(out.semantic.visible_sections,4);
    assert.equal(out.semantic.embedded_sections,3);
    const own=await searchDoctrineSituations(c,{slug:'joe-local',human:false,via:'local-token',sponsoring_human_slug:'joe',human_slug:'joe'},
      {q:'tenant tour',limit:10,content_classes:['reference']});
    assert.ok(own.hits.some(hit=>hit.section_id===hidden.section),'personal owner keeps the same vector access as lexical access');
    assert.equal(own.semantic.visible_sections,5);
    await c.query('set role carr_reader');
    const reader=await searchDoctrineSituations(c,actor,{q:'tenant tour',limit:10,content_classes:['reference']});
    assert.deepEqual(reader.hits.map(hit=>hit.section_id),out.hits.map(hit=>hit.section_id),'reader requires no base table grants');
    await c.query('reset role');
  } finally { await fixture.close(); }
});

test('backfill embeds bounded current revisions once',async()=>{
  const { doctrineEmbeddingDatabase }=await import('./helpers/doctrine-embedding-postgres.mjs');
  const { doctrineTools }=await import('../src/doctrine.js');
  const { ToolError }=await import('../src/tool-error.js');
  const verbs=doctrineTools({withEnvelope:async(_c,_a,_n,_args,fn)=>fn(),writeEvent:async()=>{},ToolError});
  assert.ok(verbs['backfill-doctrine-embeddings'],'backfill is an authenticated write verb');
  assert.equal(verbs['backfill-doctrine-embeddings'].write,true);
  const fixture=await doctrineEmbeddingDatabase();
  try{
    const c={query:fixture.client.query.bind(fixture.client),ai:{run:async(_model,{text})=>({data:text.map(()=>unit())})}};
    await seedSection(c,0);
    await seedSection(c,1);
    await seedSection(c,2,{visibility:'personal'});
    await c.query('begin');
    const out=await verbs['backfill-doctrine-embeddings'].handler(c,actor,{idempotency_key:'batch-one',limit:1});
    assert.equal(out.embedded,1);
    assert.equal(out.remaining,1,'private sections are not counted');
    const second=await verbs['backfill-doctrine-embeddings'].handler(c,actor,{idempotency_key:'batch-two',limit:16});
    assert.equal(second.embedded,1);
    assert.equal(second.remaining,0);
    const third=await verbs['backfill-doctrine-embeddings'].handler(c,actor,{idempotency_key:'batch-three',limit:16});
    assert.equal(third.embedded,0);
    assert.equal((await c.query('select count(*)::int as n from doctrine_embedding')).rows[0].n,2);
    await c.query('rollback');
  }finally{await fixture.close();}
});

for (const [name, response] of [
  ['wrong dimensions', {data:[[1,0]]}],
  ['zero vector', {data:[unit(0,0)]}],
  ['nonfinite vector', {data:[unit(Infinity)]}],
  ['wrong batch length', {data:[]}],
]) test(`invalid model output (${name}) preserves lexical search`,async()=>{
  const hit={section_id:'unchanged',final_score:1};
  const result=await searchDoctrineSituations({ai:{run:async()=>response},query:async()=>({rows:[hit]})},actor,{q:'tenant tour'});
  assert.deepEqual(result.hits,[hit]);
  assert.equal(result.semantic.reason,'embedding_failed');
});

test('cold and empty corpora report coverage without changing lexical ranks',async()=>{
  const { doctrineEmbeddingDatabase }=await import('./helpers/doctrine-embedding-postgres.mjs');
  const fixture=await doctrineEmbeddingDatabase();
  try {
    const c={query:fixture.client.query.bind(fixture.client),ai:{run:async()=>({data:[unit()]})}};
    const empty=await searchDoctrineSituations(c,actor,{q:'tenant tour'});
    assert.deepEqual(empty.hits,[]);
    assert.equal(empty.semantic.visible_sections,0);
    await seedSection(c,0,{body:'tenant tour'});
    const lexical=await searchDoctrineSituations({query:c.query},actor,{q:'tenant tour'});
    const cold=await searchDoctrineSituations(c,actor,{q:'tenant tour'});
    assert.deepEqual(cold.hits,lexical.hits);
    assert.equal(cold.semantic.status,'not_backfilled');
    assert.equal(cold.semantic.embedded_sections,0);
  } finally {await fixture.close();}
});

test('embedding storage errors roll back only optional vectors and allow a later retry',async()=>{
  const { doctrineEmbeddingDatabase }=await import('./helpers/doctrine-embedding-postgres.mjs');
  const { embedDoctrineRevisions }=await import('../src/doctrine-embeddings.js');
  const fixture=await doctrineEmbeddingDatabase();
  try {
    const c={query:fixture.client.query.bind(fixture.client),ai:{run:async()=>({data:[unit()]})}};
    const section=await seedSection(c,0);
    await c.query('begin');
    await c.query(`create function reject_fixture_embedding() returns trigger language plpgsql as $$ begin raise exception 'fixture storage fault'; end $$`);
    await c.query('create trigger reject_fixture_embedding before insert on doctrine_embedding for each row execute function reject_fixture_embedding()');
    const revision={revision_id:section.revision,plain_text:'sundial instructions'};
    const out=await embedDoctrineRevisions(c,[revision]);
    assert.equal(out.reason,'embedding_storage_failed');
    assert.equal((await c.query('select 1 as n')).rows[0].n,1,'transaction still accepts doctrine work');
    await c.query('drop trigger reject_fixture_embedding on doctrine_embedding');
    const healed=await embedDoctrineRevisions(c,[revision]);
    assert.equal(healed.embedded,1);
    await c.query('rollback');
  }finally{await fixture.close();}
});

test('backfill rejects invalid batch bounds before querying',async()=>{
  const { doctrineTools }=await import('../src/doctrine.js');
  const { ToolError }=await import('../src/tool-error.js');
  const verbs=doctrineTools({withEnvelope:async(_c,_a,_n,_args,fn)=>fn(),writeEvent:async()=>{},ToolError});
  for(const limit of [0,17,-1,1.5,'16']) {
    await assert.rejects(()=>verbs['backfill-doctrine-embeddings'].handler({query:async()=>assert.fail('invalid batch queried')},actor,
      {idempotency_key:'bad-limit',limit}),error=>error.payload.error==='embedding_batch_limit_invalid');
  }
});

for (const verb of ['write-doctrine-section','change-doctrine-sections'])
  test(`${verb} embeds the new revision through the ordinary write handler`,async()=>{
    const { doctrineTools }=await import('../src/doctrine.js');
    const { ToolError }=await import('../src/tool-error.js');
    const verbs=doctrineTools({withEnvelope:async(_c,_a,_n,_args,fn)=>fn(),writeEvent:async()=>{},ToolError});
    const modelCalls=[],stored=[];
    const revision=fixtureIds[25];
    const db={ai:{run:async(model,input)=>{modelCalls.push({model,input});return{data:input.text.map(()=>unit())};}},
      async query(text,params=[]){
        const sql=text.replace(/\s+/g,' ').trim();
        if(sql.includes('from doctrine_document')&&!sql.includes('max_age_days'))return{rows:[{id:fixtureIds[0],slug:'fixture',content_class:'reference',visibility:'shared'}]};
        if(sql.includes('max_age_days'))return{rows:[{max_age_days:null}]};
        if(sql.includes('from doctrine_section')&&!sql.startsWith('insert into doctrine_embedding'))return{rows:[{id:fixtureIds[1],current_version:1,current_revision_id:fixtureIds[2]}]};
        if(sql.startsWith('insert into doctrine_revision'))return{rows:[{id:revision}]};
        if(sql.startsWith('insert into doctrine_embedding')){stored.push(params);return{rows:[{revision_id:revision}]};}
        if(sql.includes('update doctrine_meta'))return{rows:[{generation:2}]};
        if(sql.includes('returning id'))return{rows:[{id:fixtureIds[24]}]};
        return{rows:[]};
      }};
    const item={section_key:'fixture',base_version:1,body_text:'New revision of the procedure.',title:'Mutable title'};
    const result=await verbs[verb].handler(db,{id:fixtureIds[20],slug:'joe',human:true},
      {idempotency_key:'ordinary-write',document:'fixture',...item,items:[item]});
    assert.equal(result.semantic.embedded,1);
    assert.equal(stored[0][0],revision,'embedding follows the revision produced by this write');
    assert.equal(stored[0][1],'@cf/baai/bge-small-en-v1.5:cls:chunks-v1');
    assert.equal(modelCalls[0].input.pooling,'cls');
    assert.deepEqual(modelCalls[0].input.text,[item.body_text],'mutable titles do not drift revision-keyed vectors');
  });

test('backfill uses the existing envelope for same-key response replay',async()=>{
  const { doctrineEmbeddingDatabase }=await import('./helpers/doctrine-embedding-postgres.mjs');
  const { TOOLS }=await import('../src/tools.js');
  const fixture=await doctrineEmbeddingDatabase();
  try {
    let calls=0;
    const c={query:fixture.client.query.bind(fixture.client),ai:{run:async(_model,{text})=>{calls++;return{data:text.map(()=>unit())};}}};
    await seedSection(c,0);
    const runtime={...actor,id:fixtureIds[20]};
    const args={idempotency_key:fixtureIds[21],limit:16};
    await c.query('begin');
    const first=await TOOLS['backfill-doctrine-embeddings'].handler(c,runtime,args);
    const replay=await TOOLS['backfill-doctrine-embeddings'].handler(c,runtime,args);
    assert.equal(first.embedded,1);
    assert.equal(replay.replayed,true);
    assert.equal(calls,1,'same-key retry never calls the model again');
    await c.query('rollback');
  }finally{await fixture.close();}
});

test('a timed-out embedding call returns the lexical answer',async()=>{
  const hit={section_id:'lexical-timeout',final_score:1};
  const out=await searchDoctrineSituations({ai:{run:()=>new Promise(()=>{})},query:async()=>({rows:[hit]})},actor,{q:'tenant tour'});
  assert.deepEqual(out.hits,[hit]);
  assert.equal(out.semantic.reason,'embedding_failed');
});

test('a vector-query failure returns the lexical answer',async()=>{
  const hit={section_id:'lexical-sql-failure',final_score:1};
  let calls=0;
  const out=await searchDoctrineSituations({ai:{run:async()=>({data:[unit()]})},query:async()=>{
    if(++calls===1)throw new Error('fixture vector lookup failure');
    return{rows:[hit]};
  }},actor,{q:'tenant tour'});
  assert.deepEqual(out.hits,[hit]);
  assert.equal(out.semantic.reason,'retrieval_failed');
});

test('large atomic writes batch complete overlapping UTF-8 chunks',async()=>{
  const { embedDoctrineRevisions }=await import('../src/doctrine-embeddings.js');
  const sizes=[],inputs=[];
  const text='🦉'.repeat(200);
  const out=await embedDoctrineRevisions({
    ai:{run:async(_model,{text})=>{sizes.push(text.length);inputs.push(...text);return{data:text.map(()=>unit())};}},
    query:async sql=>({rows:sql.startsWith('insert into doctrine_embedding')?[{revision_id:fixtureIds[1]}]:[]}),
  },Array.from({length:17},(_,i)=>({revision_id:fixtureIds[i],plain_text:text})));
  assert.equal(out.embedded,17);
  assert.deepEqual(sizes,[16,16,2]);
  assert.ok(inputs.every(input=>new TextEncoder().encode(input).length<=480));
  assert.ok(inputs.every(input=>!input.includes('�')),'chunks preserve complete UTF-8 characters');
  assert.equal(inputs[0].length + inputs[1].length - 48, text.length, 'chunks cover the complete section');
});

test('blank queries keep lexical abstention without calling the model',async()=>{
  const out=await searchDoctrineSituations({ai:{run:async()=>assert.fail('blank query reached the model')},query:async()=>({rows:[]})},actor,{q:'   '});
  assert.deepEqual(out.hits,[]);
  assert.equal(out.semantic.reason,'empty_query');
});


test('runtime roles can backfill and search under the hardened default function ACL', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const { backfillDoctrineEmbeddings } = await import('../src/doctrine-embeddings.js');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const c = { query: fixture.client.query.bind(fixture.client),
      ai: { run: async (_model, { text }) => ({ data: text.map(() => unit()) }) } };
    const target = await seedSection(c, 0);
    await c.query('begin');
    await c.query('set local role carr_writer');
    assert.equal((await c.query('select current_user as role')).rows[0].role, 'carr_writer');
    const out = await backfillDoctrineEmbeddings(c, null, 16);
    assert.equal(out.semantic.status, 'ready');
    assert.equal(out.embedded, 1);
    assert.equal(out.remaining, 0);
    const writer = await searchDoctrineSituations(c, actor, { q: 'tenant tour' });
    assert.equal(writer.hits[0]?.section_id, target.section);
    await c.query('set local role carr_reader');
    const reader = await searchDoctrineSituations(c, actor, { q: 'tenant tour' });
    assert.equal(reader.semantic.status, 'ready');
    assert.equal(reader.hits[0]?.section_id, target.section);
    await c.query('rollback');
  } finally { await fixture.close(); }
});

test('a vector SQL failure preserves lexical fallback and problem capture inside BEGIN', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const { TOOLS } = await import('../src/tools.js');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const c = { query: fixture.client.query.bind(fixture.client), inTransaction: true,
      ai: { run: async () => ({ data: [unit()] }) } };
    const target = await seedSection(c, 0, { body: 'tenant tour' });
    await c.query('begin');
    await c.query(`alter function search_doctrine_semantic(text,uuid,text[],integer,text,vector[])
      rename to unavailable_semantic_fixture`);
    const fallback = await searchDoctrineSituations(c, actor, { q: 'tenant tour' });
    assert.equal(fallback.semantic.reason, 'retrieval_failed');
    assert.equal(fallback.hits[0]?.section_id, target.section);
    const captured = await TOOLS['report-problem'].handler(c, { ...actor, id: fixtureIds[20] }, {
      idempotency_key: fixtureIds[21], situation: 'tenant tour', title: 'Tour packet missing',
      desired_outcome: 'Prepare the packet', acceptance_criteria: [{ id: 'AC1', text: 'Packet is ready' }],
    });
    assert.equal(captured.ok, true);
    assert.equal((await c.query('select count(*)::int as n from ops.work_request')).rows[0].n, 1);
    await c.query('commit');
  } finally { await fixture.close(); }
});

test('answer text after byte 480 is searchable and sections rank once by their best chunk', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const { backfillDoctrineEmbeddings } = await import('../src/doctrine-embeddings.js');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const inputs = [];
    const intro = 'General introductory material. '.repeat(40);
    const answer = 'Assemble the site visit packet before departure. '.repeat(30);
    const c = { query: fixture.client.query.bind(fixture.client), inTransaction: true,
      ai: { run: async (_model, { text }) => {
        inputs.push(...text);
        return { data: text.map(input => input.includes('site visit packet') || input.includes('tenant tour')
          ? unit() : unit(0, 1)) };
      } } };
    const target = await seedSection(c, 0, { body: intro + answer });
    await seedSection(c, 1, { body: intro + 'Use a sundial to tell the time.' });
    const before = await searchDoctrineSituations({ query: c.query }, actor, { q: 'tenant tour' });
    assert.equal(before.hits.length, 0, 'paraphrase shares no lexical terms with the answer');
    await c.query('begin');
    const backfill = await backfillDoctrineEmbeddings(c, null, 16);
    assert.equal(backfill.embedded, 2, 'backfill counts sections rather than chunks');
    const found = await searchDoctrineSituations(c, actor, { q: 'tenant tour' });
    assert.equal(found.hits[0]?.section_id, target.section);
    assert.equal(found.hits.filter(hit => hit.section_id === target.section).length, 1);
    assert.equal(found.hits[0].provenance.semantic_similarity, 1);
    assert.equal(found.semantic.visible_sections, 2);
    assert.equal(found.semantic.embedded_sections, 2);
    assert.ok(inputs.some(input => input.includes('site visit packet')));
    const longQuery = await searchDoctrineSituations(c, actor, { q: intro + 'tenant tour' });
    assert.equal(longQuery.hits[0]?.section_id, target.section, 'query tails also reach the model');
    assert.equal((await backfillDoctrineEmbeddings(c, null, 16)).embedded, 0);
    await c.query('rollback');
  } finally { await fixture.close(); }
});
