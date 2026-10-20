import { restoreEventIdentity } from '../../mcp-server/test/helpers/snapshot-schema.mjs';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from '../../mcp-server/test/helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from '../../mcp-server/node_modules/pg/lib/index.js';
import { TOOLS, executeRegisteredTool } from '../../mcp-server/src/tools.js';
import { readCommandCenterSummary } from '../../mcp-server/src/workspace-command-center.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const schema = readFileSync(path.join(root, 'db/schema.sql'), 'utf8');
let bin;
for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
  try {
    const candidate = execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
  } catch { /* Try the other supported PostgreSQL installations. */ }
}
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = { id: id(1), slug: 'joe', human: true, via: 'dealroom-cookie', client_id: 'dealroom-pwa' };

test('Local Deals PostgreSQL caller and evidence regressions', { skip: !bin && !process.env.CARR_CI_DATABASE_URL && 'PostgreSQL unavailable' }, async t => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let postgresFixture, dir;
  let admin;
  let database;
  let c;
  // A provided CI cluster is owned and budgeted by its caller.
  const releaseBudget = ciDsn ? async () => {} : await acquirePostgresFixtureGroup();
  try {
    let connection;
    if (ciDsn) {
      const url = new URL(ciDsn);
      assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
      assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
      admin = new pg.Client({ connectionString: ciDsn });
      await admin.connect();
      const name = `local_deals_${randomUUID().replaceAll('-', '')}`;
      await admin.query(`create database "${name}" template template0`);
      database = name;
      url.pathname = `/${database}`;
      connection = { connectionString: url.href };
    } else {
      postgresFixture = await acquireDisposablePostgres({ prefix: 'local-deals-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
      dir = postgresFixture.root;
      await postgresFixture.run(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale']);
      await postgresFixture.run(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start']);
      connection = { host: dir, user: 'fixture', database: 'postgres' };
    }
    c = new pg.Client({ ...connection,
      // Preserve PostgreSQL microseconds, as the production HTTP driver does.
      types: { getTypeParser: (oid, format) => oid === 1184 ? value => value : pg.types.getTypeParser(oid, format) },
    });
    await c.connect();
    if (process.env.CARR_CI_DATABASE_URL) {
      const isolated = (await c.query('select current_database() name')).rows[0].name;
      assert.match(isolated, /^local_deals_/);
      assert.notEqual(isolated, new URL(process.env.CARR_CI_DATABASE_URL).pathname.slice(1));
      assert.equal((await c.query("select to_regclass('public.deal') existing")).rows[0].existing, null);
    }
    // Roles belong to the cluster; an isolated database does not provide them.
    // Preserve existing shared-cluster roles and their attributes.
    await c.query(`do $$ begin
      begin create role carr_reader; exception when duplicate_object then null; end;
      begin create role carr_writer; exception when duplicate_object then null; end;
    end $$;`);
    // Use the committed table definitions and caller views, without production data.
    for (const name of ['actor', 'party', 'client', 'deal', 'deal_phase', 'deal_participant', 'next_action', 'deal_note', 'national_account_owner', 'deal_market_assignment', 'deal_review_item', 'deal_review_session', 'event', 'tool_call', 'deal_conflict', 'critical_date', 'lease', 'activity', 'premises', 'negotiation_round', 'document', 'commission', 'capture_post_call_action', 'building', 'space', 'premises_space', 'lead', 'lead_stage', 'lead_lane', 'lead_stage_move']) {
      const table = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
      assert.ok(table, name);
      await c.query(table);
    }
    await restoreEventIdentity(c, schema);
    await c.query(schema.match(/CREATE FUNCTION public\.trg_touch_row\(\)[\s\S]*?\$\$;/)[0]);
    await c.query('create trigger client_touch before update on client for each row execute function trg_touch_row()');
    await c.query('create trigger lead_touch before update on lead for each row execute function trg_touch_row()');
    await c.query('create sequence ref_lead_seq start 900000');
    await c.query('alter table tool_call add primary key(idempotency_key);');
    await c.query("create view v_last_touch as select null::text subject_type, null::uuid subject_id, null::date last_touch where false;");
    for (const name of ['v_client_account', 'v_deal_board', 'v_deal_room_board', 'v_deal_room_account', 'v_deal_room_event', 'v_deal_room_session', 'v_deal_reconciliation_read', 'v_deal_room_note', 'v_deal_room_critical_date', 'v_deal_room_action', 'v_deal_room_activity', 'v_deal_room_participant', 'v_deal_room_premises', 'v_deal_room_negotiation', 'v_deal_room_document', 'v_deal_room_phase_change', 'v_deal_room_current_lease', 'v_client_engagement_status', 'v_lead_board', 'v_lead_board_stage', 'v_lead_client_link', 'v_lead_stage_transition', 'v_lead_prospect_engagement', 'v_lead_workspace_lifecycle', 'v_lead_workspace', 'v_lead_workspace_detail']) {
      const view = schema.match(new RegExp(`CREATE VIEW public\\.${name} AS[\\s\\S]*?;`))?.[0];
      assert.ok(view, name);
      await c.query(view);
      await c.query(`grant select on ${name} to carr_reader,carr_writer`);
    }
    await c.query("create view v_ref_index as select 'deal'::text subject_type, id subject_id from deal union all select 'lead', id from lead;");
    await c.query("grant select on v_ref_index to carr_reader");
    await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'joe','Synthetic Partner','human',true)", [actor.id]);
    for (const [slug, sort] of [['research', 1], ['negotiation', 2], ['legal', 3], ['closed', 4]]) {
      await c.query('insert into deal_phase(slug,label,sort) values($1,$1,$2)', [slug, sort]);
    }
    for (const [slug, sort] of [['nurture_drip',50],['opportunity',60],['archived',100],['do_not_contact',110]])
      await c.query('insert into lead_stage(slug,label,sort) values($1,$1,$2)', [slug,sort]);
    const fixture = async national => {
      await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Practice',$2,$2)", [id(2), actor.id]);
      await c.query('insert into client(id,party_id,created_by,updated_by) values($1,$2,$3,$3)', [id(3), id(2), actor.id]);
      if (national) {
        await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Account',$2,$2)", [id(5), actor.id]);
        await c.query('update party set org_id=$1 where id=$2', [id(5), id(2)]);
        await c.query("insert into client(id,party_id,client_type,created_by,updated_by) values($1,$2,'national_account',$3,$3)", [id(6), id(5), actor.id]);
      }
      await c.query("insert into deal(id,client_id,name,deal_type,phase,owner,attention,next_date,created_by,updated_by) values($1,$2,'Synthetic Assignment','renewal','research','joe',true,current_date-1,$3,$3)", [id(4), id(3), actor.id]);
    };
    const transaction = async fn => {
      await c.query('begin');
      try { await fn(); } finally { await c.query('rollback'); }
    };


    for (const state of [{stage:'archived',suppressed:false},{stage:'do_not_contact',suppressed:true},{stage:'nurture_drip',suppressed:true}])
      await t.test(`protected lead ${JSON.stringify(state)}`,async()=>transaction(async()=>{
        await fixture(false);
        await c.query("update client set status='prospect' where id=$1",[id(3)]);
        await c.query("insert into lead(id,registry_ref,party_id,client_id,stage,suppressed,notes,score,created_by,updated_by) values($1,'L-900001',$2,$3,$4,$5,'Synthetic protected history',72,$6,$6)",[id(30),id(2),id(3),state.stage,state.suppressed,actor.id]);
        const before=(await c.query('select * from lead where id=$1',[id(30)])).rows[0];
        const result=await executeRegisteredTool(c,actor,'set-prospect-engagement-status',{client_id:id(3),base_version:2,status:'inactive',reason:'Synthetic deferral',idempotency_key:'protected-inactive'});
        assert.deepEqual((await c.query('select * from lead where id=$1',[id(30)])).rows[0],before);
        await assert.rejects(executeRegisteredTool(c,actor,'set-prospect-engagement-status',{client_id:id(3),base_version:result.client_version,status:'active',reason:'Synthetic return',idempotency_key:'protected-active'}),e=>e.payload?.error==='prospect_lead_protected');
        assert.deepEqual((await c.query('select * from lead where id=$1',[id(30)])).rows[0],before);
      }));
    await t.test('existing prospect survives migration with active default and unchanged version',async()=>transaction(async()=>{
      await fixture(false);
      await c.query("update client set status='prospect' where id=$1",[id(3)]);
      const before=(await c.query('select * from deal where id=$1',[id(4)])).rows[0];
      await c.query('drop view v_client_engagement_status,v_lead_prospect_engagement');
      await c.query('alter table client drop column prospect_engagement_status cascade');
      await c.query(readFileSync(path.join(root,'migrations/0915_prospect_engagement_status.sql'),'utf8'));
      await c.query(schema.match(/CREATE VIEW public\.v_deal_room_account AS[\s\S]*?;/)[0]);
      assert.deepEqual((await c.query('select * from deal where id=$1',[id(4)])).rows[0],before);
      assert.deepEqual((await c.query('select version,prospect_engagement_status from client where id=$1',[id(3)])).rows[0],{version:2,prospect_engagement_status:'active'});
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c,actor,{})).deals.map(d=>d.id),[id(4)]);
    }));
    const app = path.resolve(root, '../doctorcre-app');
    const { chromium } = await import(path.join(app,'node_modules/playwright/index.mjs'));
    const { mkdirSync, writeFileSync } = await import('node:fs');
    const evidence = path.join(root,'out/prospect-review');
    mkdirSync(evidence,{recursive:true});
    const browser = await chromium.launch();
    try {
      for (const width of [390,1280]) await t.test(`real PostgreSQL rendered round trip ${width}`, async () => transaction(async () => {
        await fixture(false);
        await c.query("update client set status='prospect' where id=$1",[id(3)]);
        await c.query("insert into lead(id,registry_ref,party_id,client_id,stage,notes,score,created_by,updated_by) values($1,'L-900001',$2,$3,'opportunity','Synthetic retained plans',72,$4,$4)",[id(30),id(2),id(3),actor.id]);
        await c.query("insert into deal_note(id,deal_id,kind,text,actor_id) values($1,$2,'note','Synthetic retained deal history',$3)",[id(35),id(4),actor.id]);
        const beforeDeal=(await c.query('select * from deal where id=$1',[id(4)])).rows[0];
        const beforeNote=(await c.query('select * from deal_note where id=$1',[id(35)])).rows[0];
        const context=await browser.newContext({viewport:{width,height:960},reducedMotion:'reduce'});
        const page=await context.newPage();
        const errors=[],writes=[];
        let loseNextResponse = false;
        page.on('pageerror',e=>errors.push(e.message));
        await context.route('**/*',async route=>{
          const req=route.request(),url=new URL(req.url());
          if(url.origin!=='http://localhost')return route.abort();
          if(url.pathname==='/mcp'){
            const input=req.postDataJSON(),{name,arguments:args}=input.params;
            try {
              let payload;
              if(['deal-room-board','get-deal-room','lead-board','set-prospect-engagement-status'].includes(name)){
                payload=await executeRegisteredTool(c,actor,name,args);
                if(name==='set-prospect-engagement-status') {
                  writes.push(args);
                  if (loseNextResponse) {
                    loseNextResponse = false;
                    return route.fulfill({json:{jsonrpc:'2.0',id:input.id,result:{}}});
                  }
                }
              }else if(name==='presence-lease')payload={ok:true};
              else payload={};
              return route.fulfill({json:{jsonrpc:'2.0',id:input.id,result:{content:[{type:'text',text:JSON.stringify(payload)}]}}});
            }catch(e){return route.fulfill({json:{jsonrpc:'2.0',id:input.id,result:{isError:true,content:[{type:'text',text:JSON.stringify(e.payload||{error:e.message})}]}}});}
          }
          if(url.pathname==='/pipeline/changes')return route.fulfill({json:{events:[],cursor:'0',presence:[]}});
          if(url.pathname.startsWith('/api/')||url.pathname==='/app-release')return route.fulfill({json:{actor:{slug:'joe'}}});
          const file=url.pathname==='/deals'?'pipeline.html':url.pathname==='/leads'?'leads.html':url.pathname.slice(1);
          try{return route.fulfill({body:readFileSync(path.join(app,file)),contentType:/\.m?js$/.test(file)?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.json')?'application/json':'text/html'});}
          catch{return route.fulfill({status:404,body:''});}
        });
        try{
          await page.goto('http://localhost/deals?mode=live');
          await page.locator(`.kanban-column [data-id="${id(4)}"] .card-open`).click();
          await page.locator('#detailInactiveForm').waitFor();
          await page.screenshot({path:path.join(evidence,`${width}-before-inactive.png`)});
          await page.locator('#detailInactiveForm input').fill('Synthetic deferred project');
          await page.locator('#detailInactiveForm button').click();
          await page.waitForFunction(()=>!document.querySelector('#recordPanel').open);
          assert.equal((await TOOLS['deal-room-board'].handler(c,actor,{})).deals.length,0);
          await page.goto('http://localhost/leads');
          await page.locator(`[data-lead-id="${id(30)}"]`).first().click();
          await page.locator('#activateProspect').waitFor();
          assert.match(await page.locator('#detailBody').innerText(),/Synthetic retained plans/);
          await page.screenshot({path:path.join(evidence,`${width}-before-active.png`)});
          await page.locator('#activateProspect input').fill('Synthetic renewed interest');
          await page.locator('#activateProspect button').click();
          await page.waitForFunction(()=>document.querySelector('#moveAnnouncement').textContent.includes('returned'));
          const board=await TOOLS['deal-room-board'].handler(c,actor,{});
          assert.deepEqual(board.deals.map(d=>d.id),[id(4)]);
          assert.equal(writes.length,2);
          assert.equal(writes[0].base_version,2);
          assert.equal(writes[1].base_version,3);
          assert.notEqual(writes[0].idempotency_key,writes[1].idempotency_key);
          await page.goto('http://localhost/deals?mode=live');
          await page.locator(`.kanban-column [data-id="${id(4)}"]`).waitFor();
          assert.equal(await page.locator('.prospect-group,[data-engagement="inactive"]').count(),0);
          assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
          await page.locator(`.kanban-column [data-id="${id(4)}"] .card-open`).click();
          await page.getByText('Synthetic retained deal history',{exact:true}).waitFor();
          await page.screenshot({path:path.join(evidence,`${width}-restored.png`)});
          assert.deepEqual((await c.query('select * from deal where id=$1',[id(4)])).rows[0],beforeDeal);
          assert.deepEqual((await c.query('select * from deal_note where id=$1',[id(35)])).rows[0],beforeNote);
          const lead=(await c.query('select * from lead where id=$1',[id(30)])).rows[0];
          assert.equal(lead.notes,'Synthetic retained plans');assert.equal(lead.score,'72.00');assert.equal(lead.client_id,id(3));assert.equal(lead.party_id,id(2));
          assert.deepEqual(errors,[]);
          writeFileSync(path.join(evidence,`${width}-writes.json`),JSON.stringify({writes,errors,deal_preserved:true,note_preserved:true,score:lead.score},null,2));
          await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Second Prospect',$2,$2)",[id(40),actor.id]);
          await c.query("insert into client(id,party_id,status,created_by,updated_by) values($1,$2,'prospect',$3,$3)",[id(41),id(40),actor.id]);
          await c.query("insert into deal(id,client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,$2,'Synthetic Second Assignment','renewal','research','joe',$3,$3)",[id(42),id(41),actor.id]);
          loseNextResponse = true;
          await page.locator('#detailInactiveForm input').fill('Synthetic lost confirmation');
          await page.locator('#detailInactiveForm button').click();
          await page.locator(`.kanban-column [data-id="${id(42)}"]`).waitFor();
          await page.getByLabel('Close deal',{exact:true}).click();
          await page.locator(`.kanban-column [data-id="${id(42)}"] .card-open`).click();
          await page.locator('#detailInactiveForm input').fill('Synthetic second deferral');
          await page.locator('#detailInactiveForm button').click();
          await page.getByText('Check the pending prospect change before starting another.',{exact:true}).waitFor();
          assert.equal(writes.length,3);
          assert.equal((await c.query('select prospect_engagement_status from client where id=$1',[id(41)])).rows[0].prospect_engagement_status,'active');
          await page.screenshot({path:path.join(evidence,`${width}-stuck-pending.png`)});
          writeFileSync(path.join(evidence,`${width}-pending-defect.json`),JSON.stringify({committed_first_transition:true,original_card_absent:await page.locator(`.kanban-column [data-id="${id(4)}"]`).count()===0,second_transition_blocked:true,mutation_count:writes.length},null,2));
        }finally{await context.close();}
      }));
    }finally{await browser.close();}
  } finally {
    try {
      if (c) await c.end();
      if (admin) {
        try { if (database) await admin.query(`drop database "${database}"`); }
        finally { await admin.end(); }
      }
    } finally {
      try { await postgresFixture?.close(); } finally { await releaseBudget(); }
    }
  }
});
