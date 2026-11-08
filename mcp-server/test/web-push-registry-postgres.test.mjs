import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync, execFileSync } from 'node:child_process';
import { randomUUID, webcrypto } from 'node:crypto';
import pg from 'pg';
import { Validator } from '@cfworker/json-schema';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { webPushTools } from '../src/web-push-subscriptions.ts';
import { ToolError } from '../src/tool-error.ts';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

const partner = { id: '20000000-0000-4000-8000-000000000001', human: true, slug: 'joe', web_push_session: 'a'.repeat(64) };
const other = { id: '20000000-0000-4000-8000-000000000002', human: true, slug: 'dell', web_push_session: 'b'.repeat(64) };
const tools = webPushTools({ ToolError });
const responseContract = new Validator(JSON.parse(await readFile(new URL('../contracts/web-push-registry.v1.json',import.meta.url),'utf8')));
const register = 'register-web-push-subscription-v1';
const revoke = 'revoke-web-push-subscription-v1';
const list = 'read-web-push-subscriptions-v1';
const logout = 'invalidate-web-push-session-v1';
const publicKey = await webcrypto.subtle.exportKey('raw', (await webcrypto.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},true,['deriveBits'])).publicKey);
const subscription = () => ({ installation_id: randomUUID(), idempotency_key: randomUUID(), base_generation: 0,
  endpoint: `https://web.push.apple.com/${randomUUID()}`, p256dh: Buffer.from(publicKey).toString('base64url'),
  auth: Buffer.alloc(16,1).toString('base64url'), vapid_key_version: 'v1' });

test('registry lifecycle, isolation, replay and logout on disposable PostgreSQL', async t => {
  const db = await postgresDatabase('push-registry-');
  const c = db.client;
  const invoke = async (name, args, actor=partner, client=c) => {
    await client.query('begin');
    try {
      await client.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true)", [actor.slug]);
      const result = await tools[name].handler(client, actor, args);
      assert.equal(responseContract.validate(result).valid,true);
      await client.query('commit');
      return result;
    } catch (e) { await client.query('rollback'); throw e; }
  };
  const refused = (promise, code) => assert.rejects(promise, e => e.payload?.error === code);
  try {
    await c.query("create schema ops; create table public.actor(id uuid primary key,slug text,active boolean,kind text)");
    await c.query("insert into actor values ($1,'joe',true,'human'),($2,'dell',true,'human')",[partner.id,other.id]);
    const schema = await readFile(new URL('../../db/schema.sql',import.meta.url),'utf8');
    const identityFunction = schema.match(/CREATE FUNCTION ops\.portfolio_writer_actor_id\(\)[\s\S]*?\n\$\$;/);
    assert.ok(identityFunction);
    await c.query(identityFunction[0]);
    await c.query("create function ops.scac_current_grant_contract() returns jsonb language sql as $$select '{\"secdef_execute\":[]}'::jsonb$$");
    for (const role of ['carr_reader','carr_writer','carr_authority','carr_jobs','dot_reader']) await c.query(`create role ${role}`);
    await c.query(await readFile(new URL('../../migrations/1163_web_push_registry.sql',import.meta.url),'utf8'));
    assert.equal((await c.query("select has_table_privilege('carr_writer','ops.web_push_subscription','SELECT') as allowed")).rows[0].allowed,false);
    const args = subscription();
    await t.test('Register and duplicate request preserve one confirmed generation', async () => {
      const first = await invoke(register,args);
      assert.equal(first.generation,1);
      assert.equal((await invoke(register,args)).replayed,true);
      assert.equal((await invoke(list,{})).subscriptions.length,1);
    });
    await t.test('release-aware read-only proof CLI accepts recorded contracts and refuses false evidence', async () => {
      const root = await mkdtemp(join(tmpdir(), 'push-proof-'));
      const cli = new URL('../../ops/web-push-registry-proof.py', import.meta.url).pathname;
      const git = (...args) => execFileSync('git', ['-c','user.name=Fixture','-c','user.email=fixture@example.invalid',...args],
        {cwd:root, encoding:'utf8', input:'', env:{...process.env,GIT_CONFIG_GLOBAL:'/dev/null',GIT_CONFIG_NOSYSTEM:'1'}}).trim();
      try {
        git('init','-q');
        const tree=git('mktree');
        const older=git('commit-tree',tree,'-m','older');
        const merge=git('commit-tree',tree,'-p',older,'-m','merge');
        const descendant=git('commit-tree',tree,'-p',merge,'-m','descendant');
        const unrelated=git('commit-tree',tree,'-m','unrelated');
        const catalog = await TOOLS['list-verbs'].handler(c, partner, {filter:'web-push'});
        const registry = await invoke(list,{});
        assert.equal(responseContract.validate(registry).valid,true);
        const bundle = {release:{ok:true,env:{value:'production'},git_sha:{value:merge}},
          provenance:{release_sha:merge,actor_slug:'joe',transport:'signed-in-app-origin/mcp',
            app_origin:'https://app.doctorcre.com',queries:[{verb:'list-verbs',args:{filter:'web-push'}},
              {verb:list,args:{}}]},catalog,registry};
        const run = async (value,expected,label) => {
          const path=join(root,'evidence.json');
          await writeFile(path,JSON.stringify(value));
          const result=spawnSync(process.env.CARR_CI_PYTHON || 'python3',[cli,'--fixture','--evidence',path,
            '--merge-sha',merge,'--repository',root],{encoding:'utf8',timeout:10000});
          assert.equal(result.status,expected,label+': '+result.stdout+result.stderr);
          console.log('proof fixture '+label+' exit '+result.status);
        };
        for (const [label,sha,code] of [['equal',merge,0],['descendant',descendant,0],['older',older,78],
          ['unrelated',unrelated,78],['unknown','f'.repeat(40),78]]) {
          const value=structuredClone(bundle);
          value.release.git_sha.value=sha; value.provenance.release_sha=sha;
          await run(value,code,label);
        }
        const liveEvidence=join(root,'live.json');
        await writeFile(liveEvidence,JSON.stringify(bundle));
        const live = spawnSync(process.env.CARR_CI_PYTHON || 'python3',['-c',`
import importlib.util, json, sys, time
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('proof',sys.argv[1])
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
b=json.load(open(sys.argv[2])); calls=[]
def read(url, **kwargs):
    calls.append(url)
    return SimpleNamespace(status=200,body=json.dumps(b['release']).encode())
p.read=read
args=['--evidence',sys.argv[2],'--merge-sha',sys.argv[3],'--repository',sys.argv[4]]
assert p.main(args)==0
assert calls==['https://api.doctorcre.com/release']
def stalled(*args, **kwargs):
    time.sleep(1)
p.read=stalled
assert p.main(args+['--timeout','0.05'])==78
`,cli,liveEvidence,merge,root],{encoding:'utf8',timeout:10000});
        assert.equal(live.status,0,live.stdout+live.stderr);
        const missing=structuredClone(bundle);
        missing.catalog.verbs=missing.catalog.verbs.filter(v=>v.name!==register);
        await run(missing,1,'missing registered verb');
        const malformed=structuredClone(bundle);malformed.registry.subscriptions[0].endpoint='secret';
        await run(malformed,1,'response contract violation');
        const refused=structuredClone(bundle);refused.registry={error:'push_session_required'};
        await run(refused,78,'no authenticated session');
        const stale=structuredClone(bundle);stale.provenance.release_sha=older;
        await run(stale,78,'read release mismatch');
      } finally { await rm(root,{recursive:true,force:true}); }
    });
    await t.test('concurrent duplicate requests return one receipt', async () => {
      const second = new pg.Client(c.connectionParameters);
      await second.connect();
      try {
        const replacement = {...args,base_generation:1,idempotency_key:randomUUID()};
        const replies=await Promise.all([invoke(register,replacement),invoke(register,replacement,partner,second)]);
        assert.equal(replies[0].generation,2);
        assert.equal(replies[1].generation,2);
        assert.equal(replies.filter(r=>r.replayed===true).length,1);
      } finally { await second.end(); }
    });
    await t.test('changed arguments under one key refuse', async () => {
      await refused(invoke(register,{...args, endpoint:'https://web.push.apple.com/changed'}),'push_key_reuse');
    });
    await t.test('foreign access and endpoint transfer refuse without secrets', async () => {
      assert.deepEqual((await invoke(list,{},other)).subscriptions,[]);
      await refused(invoke(revoke,{installation_id:args.installation_id,base_generation:2,idempotency_key:randomUUID()},other),'push_subscription_unavailable');
      await refused(invoke(register,{...subscription(),endpoint:args.endpoint},other),'push_subscription_unavailable');
      const response = JSON.stringify(await invoke(list,{}));
      for (const secret of [args.endpoint,args.auth,args.p256dh]) assert.ok(!response.includes(secret));
    });
    await t.test('endpoint and encryption key validation refuses secret-free', async () => {
      for (const endpoint of ['http://web.push.apple.com/a','https://127.0.0.1/a','https://web.push.apple.com.evil/a','https://'+'synthetic'+'@'+'web.push.apple.com/a','https://web.push.apple.com:443/a','https://web.push.apple.com/a#frag','https://web.push.apple.com/'+ 'a'.repeat(2050)]) {
        await refused(invoke(register,{...subscription(),endpoint}),'push_invalid_subscription');
      }
      for (const change of [{p256dh:'secret'},{auth:'secret'},{vapid_key_version:'wrong'},{p256dh:'B'+'A'.repeat(86)}]) {
        await refused(invoke(register,{...subscription(),...change}),'push_invalid_subscription');
      }
    });
    await t.test('Replace and concurrent replacement versus revocation fence stale generation', async () => {
      const second = new pg.Client(c.connectionParameters);
      await second.connect();
      try {
        const replace = {...args,idempotency_key:randomUUID(),base_generation:2};
        const remove = {installation_id:args.installation_id,idempotency_key:randomUUID(),base_generation:2};
        const results = await Promise.allSettled([invoke(register,replace),invoke(revoke,remove,partner,second)]);
        assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
        assert.equal(results.find(r=>r.status==='rejected').reason.payload.error,'push_generation_conflict');
      } finally { await second.end(); }
    });
    await t.test('Revoke and Enable advance generations and scrub revoked secrets', async () => {
      let row=(await invoke(list,{})).subscriptions[0];
      await invoke(revoke,{installation_id:args.installation_id,base_generation:row.generation,idempotency_key:randomUUID()});
      assert.equal((await c.query('select endpoint,p256dh,auth from ops.web_push_subscription')).rows[0].endpoint,null);
      row=(await invoke(list,{})).subscriptions[0];
      assert.equal((await invoke(register,{...args,base_generation:row.generation,idempotency_key:randomUUID()})).state,'active');
    });
    await t.test('logout preserves another installation and endpoint ownership survives revocation', async () => {
      const deviceActor={...partner,web_push_session:'c'.repeat(64)};
      const device=subscription();
      await invoke(register,device,deviceActor);
      await invoke(logout,{},deviceActor);
      assert.equal((await invoke(list,{})).subscriptions.filter(s=>s.state==='active').length,1);
      await refused(invoke(register,{...subscription(),endpoint:device.endpoint},other),'push_subscription_unavailable');
    });
    await t.test('database failures return only a fixed error', async () => {
      await assert.rejects(tools[list].handler({query:async()=>{throw new Error(args.endpoint+args.auth+args.p256dh);}},partner,{}),
        e=>JSON.stringify(e.payload)==='{"error":"push_registry_unavailable"}');
    });
    await t.test('three active installations and twenty mutations per minute are bounded', async () => {
      for (const hash of ['d','e','f']) await invoke(register,subscription(),{...other,web_push_session:hash.repeat(64)});
      await refused(invoke(register,subscription(),{...other,web_push_session:'1'.repeat(64)}),'push_installation_limit');
      await c.query("insert into ops.web_push_receipt(operation_key,actor_id,request_hash,response) select gen_random_uuid(),$1,'synthetic','{}'::jsonb from generate_series(1,20)",[other.id]);
      await refused(invoke(register,subscription(),other),'push_rate_limited');
    });
    await t.test('Logout from Active, Off and Revoked is idempotent and rejects old-session registration', async () => {
      await invoke(logout,{});
      await invoke(logout,{});
      assert.ok((await invoke(list,{})).subscriptions.every(s=>s.state==='revoked'));
      await refused(invoke(register,args),'push_session_closed');
      await invoke(logout,{},other);
      await refused(invoke(register,subscription(),other),'push_session_closed');
    });
    await t.test('a new authenticated session can re-enable the same owned installation', async () => {
      const row=(await invoke(list,{})).subscriptions.find(s=>s.installation_id===args.installation_id);
      const newSession={...partner,web_push_session:'9'.repeat(64)};
      assert.equal((await invoke(register,{...args,base_generation:row.generation,idempotency_key:randomUUID()},newSession)).state,'active');
      await invoke(logout,{},partner);
      assert.equal((await invoke(list,{})).subscriptions.find(s=>s.installation_id===args.installation_id).state,'active');
      await invoke(logout,{},newSession);
    });
    await t.test('unauthenticated identity and caller-selected partner refuse', async () => {
      await refused(invoke(register,subscription(),{...partner,human:false}),'push_session_required');
      await refused(invoke(register,{...subscription(),actor_id:other.id}),'push_invalid_request');
    });
  } finally { await db.close(); }
});

test('registered contract errors never echo invalid sensitive arguments', async () => {
  const secret = 'private-test-marker';
  for (const change of [{vapid_key_version:secret},{endpoint:secret.repeat(300)},{p256dh:secret.repeat(10)},{auth:secret.repeat(10)}]) {
    await assert.rejects(executeRegisteredTool({query:async()=>{throw new Error('validation reached database');}},partner,register,{...subscription(),...change}),
      error => !JSON.stringify(error.payload).includes(secret));
  }
});
