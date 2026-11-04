import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { randomUUID } from 'node:crypto';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { webPushTools } from '../src/web-push-subscriptions.ts';
import { ToolError } from '../src/tool-error.ts';
const publicKey = Buffer.concat([Buffer.from([4]), Buffer.alloc(64,7)]).toString('base64url');
const auth = Buffer.alloc(16,9).toString('base64url');
const config = () => ({ WEB_PUSH_VAPID_PUBLIC_KEY: publicKey, WEB_PUSH_KEY_GENERATION: 'synthetic-1' });
const input = (installation_id = randomUUID()) => ({ installation_id, endpoint:'https://push.example.test/private',p256dh:publicKey,auth,expirationTime:null,key_generation:'synthetic-1' });
test('subscription lifecycle enforces ownership quota replay expiry grants and no preference writes on PostgreSQL', async t => {
  const f = await acquireDisposablePostgres({prefix:'web-push-',snapshot:true});
  const pool = new pg.Pool(f.connection);
  const c = await pool.connect();
  t.after(async () => {c.release(); await pool.end(); await f.close();});
  const actors = (await c.query("select id,slug from actor where slug in ('joe','dell') order by slug")).rows;
  assert.equal(actors.length,2);
  const prefs = (await c.query('select * from ops.notification_preference order by actor')).rows;
  const run = async (request, key=randomUUID(), operation='register', actor=actors[0], tenant='carr-internal') => {
    const db = await pool.connect();
    try {
      await db.query('begin');
      await db.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true),set_config('carr.organization_tenant_id',$2,true)",[actor.slug,tenant]);
      await db.query('set local role carr_writer');
      const result = (await db.query('select ops.web_push_lifecycle($1::jsonb,$2::uuid,$3::text) as result',[request,key,operation])).rows[0].result;
      await db.query('commit'); return result;
    } catch(e) {await db.query('rollback');throw e;} finally {db.release();}
  };
  for(const patch of [{endpoint:'http://push.test/x'},{endpoint:'https://user:pw@push.test/x'},{auth:'x'.repeat(22)},{p256dh:'B'.repeat(87)}])
    assert.equal((await run({...input(),...patch})).error,'web_push_invalid_input');
  const original=input(), key=randomUUID();
  const first=await run(original,key);
  assert.equal(first.ok,true,JSON.stringify(first)); assert.equal(first.generation,1);
  assert.equal(JSON.stringify(first).includes(original.endpoint),false);
  assert.deepEqual(await run(original,key),first);
  assert.equal((await run({...original,auth:Buffer.alloc(16,2).toString('base64url')},key)).error,'key_reuse');
  assert.deepEqual(await run(original),first);
  const changed=await run({...original,endpoint:'https://push.example.test/replaced'});
  assert.equal(changed.generation,2); assert.notEqual(changed.subscription_id,first.subscription_id);
  assert.equal((await run({installation_id:original.installation_id},randomUUID(),'revoke',actors[1])).state,'absent');
  assert.equal((await c.query('select revoked_at from ops.web_push_subscription where subscription_id=$1',[changed.subscription_id])).rows[0].revoked_at,null);
  const revoked=await run({installation_id:original.installation_id},randomUUID(),'revoke');
  assert.equal(revoked.state,'revoked');
  assert.deepEqual(await run({installation_id:original.installation_id},randomUUID(),'revoke'),revoked);
  assert.deepEqual(await run(original,key),first,'old action replays without resurrection');
  assert.ok((await c.query('select revoked_at from ops.web_push_subscription where subscription_id=$1',[first.subscription_id])).rows[0].revoked_at);
  const reactivated=await run(original); assert.equal(reactivated.generation,3);
  await c.query("update ops.web_push_subscription set expires_at=clock_timestamp()-interval '1 second' where subscription_id=$1",[reactivated.subscription_id]);
  assert.equal((await run({installation_id:original.installation_id},randomUUID(),'revoke')).state,'revoked','expired version may be revoked');
  assert.equal((await run(original)).generation,4);
  assert.equal((await run({...input(),expirationTime:1})).error,'web_push_invalid_input');
  await c.query("update ops.web_push_action set created_at=clock_timestamp()-interval '2 minutes' where actor_id=$1",[actors[0].id]);
  const concurrent=await Promise.all(Array.from({length:6},()=>run(input())));
  assert.equal(concurrent.filter(r=>r.ok).length,2);
  assert.equal(concurrent.filter(r=>r.error==='web_push_limit').length,4);
  assert.equal((await c.query("select count(*)::int n from ops.web_push_subscription where actor_id=$1 and revoked_at is null and (expires_at is null or expires_at>clock_timestamp())",[actors[0].id])).rows[0].n,3);
  for(let i=0;i<8;i++) assert.equal((await run({installation_id:randomUUID()},randomUUID(),'revoke')).ok,true);
  assert.equal((await run({installation_id:randomUUID()},randomUUID(),'revoke')).error,'web_push_rate_limit');
  assert.equal((await run(input(original.installation_id),randomUUID(),'register',actors[1],'synthetic-tenant')).generation,1);
  assert.equal((await c.query("select has_table_privilege('carr_writer','ops.web_push_subscription','select') as allowed")).rows[0].allowed,false);
  for(const role of ['carr_reader','carr_jobs','carr_authority']) assert.equal((await c.query("select has_function_privilege($1,'ops.web_push_lifecycle(jsonb,uuid,text)','execute') as allowed",[role])).rows[0].allowed,false);
  assert.deepEqual((await c.query('select * from ops.notification_preference order by actor')).rows,prefs);
  for(let i=0;i<20;i++) {
    const actor={id:randomUUID(),slug:`synthetic-push-${i}`};
    await c.query("insert into actor(id,slug,kind,display_name) values($1,$2,'automation','Synthetic push actor')",[actor.id,actor.slug]);
    assert.equal((await run(input(original.installation_id),randomUUID(),'register',actor)).generation,1);
  }
  const tools=webPushTools({ToolError,config,withEnvelope:async(db,_a,_v,_x,fn)=>fn()});
  await c.query('begin');
  await c.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true),set_config('carr.organization_tenant_id','synthetic-verb-tenant',true)",[actors[0].slug]);
  await c.query('set local role carr_writer');
  const {key_generation,...browserInput}=input();
  const throughVerb=await tools['register-web-push-subscription'].handler(c,actors[0],{...browserInput,idempotency_key:randomUUID()});
  assert.equal(throughVerb.state,'active');
  await c.query('commit');
});
