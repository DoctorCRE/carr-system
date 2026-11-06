import test from 'node:test';
import assert from 'node:assert/strict';
import { createDealroomHandler } from '../src/dealroom-web.js';
import { slugForEmail } from '../src/identity.js';
import { readMailNeedsPartner } from '../src/mail-needs-partner.ts';
const TEST_PARTNER_EMAIL='partner@example.test';
const PRODUCTION_HOST='dealroom.doctorcre.com';
class MemoryKv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) {
    const value = this.values.get(key);
    if (value === undefined) return null;
    return options?.type === "json" ? JSON.parse(value) : value;
  }
  async delete(key) { this.values.delete(key); }
}

class ShellAssets {
  async fetch(request) {
    const pathname = new URL(request.url).pathname;
    if (!pathname.startsWith("/public-shell/")) return new Response("missing", { status: 404 });
    return new Response(`<!doctype html><main>Asset fixture ${pathname}</main>`);
  }
}

function env(host = PRODUCTION_HOST) {
  return {
    DEALROOM_HOST: host,
    GOOGLE_CLIENT_ID: "google-client.test",
    GOOGLE_CLIENT_SECRET: "not-a-real-secret",
    OAUTH_KV: new MemoryKv(),
    ASSETS: new ShellAssets(),
  };
}

function setCookies(response) {
  return typeof response.headers.getSetCookie === "function"
    ? response.headers.getSetCookie()
    : [response.headers.get("set-cookie")].filter(Boolean);
}

function namedCookie(response, name) {
  const header = setCookies(response).find((value) => value.startsWith(`${name}=`));
  assert.ok(header, `missing ${name} cookie`);
  return header.split(";", 1)[0];
}

async function login(handler, environment, email, { host = environment.DEALROOM_HOST, returnTo = "/" } = {}) {
  const start = await handler.fetch(new Request(
    `https://${host}/auth/login?return_to=${encodeURIComponent(returnTo)}`,
  ), environment, {});
  assert.equal(start.status, 302);
  const google = new URL(start.headers.get("location"));
  assert.equal(google.hostname, "accounts.google.com");
  assert.equal(google.searchParams.get("redirect_uri"), `https://${host}/auth/callback`);
  const state = google.searchParams.get("state");
  const pendingCookie = namedCookie(start, "__Host-dealroom_oauth");
  const callback = await handler.fetch(new Request(
    `https://${host}/auth/callback?state=${state}&code=stub-code`,
    { headers: { cookie: pendingCookie, "x-test-email": email } },
  ), environment, {});
  return callback;
}

function identityOverrides(email, clock) {
  return {
    slugForEmailFn: value => value === TEST_PARTNER_EMAIL ? "joe" : slugForEmail(value),
    exchangeGoogleCodeFn: async () => ({ id_token: "stub-id-token" }),
    verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
    now: () => clock?.now ?? 1_800_000_000_000,
    mcpHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "mcp", actor }), { headers: { "content-type": "application/json" } }),
    pipelineHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "pipeline", actor }), { headers: { "content-type": "application/json" } }),
  };
}

function identityHandler(email, clock) {
  return createDealroomHandler(identityOverrides(email, clock));
}


test('fixed GET shares parsed response, verified mailbox and source failures', async () => {
 for (const slug of ['joe','dell']) {
  const environment=env();
  environment.VERB_REQUEST_BUDGET={idFromName:name=>name,get:()=>({fetch:async()=>Response.json({allowed:true})})};
  environment.VERB_REQUEST_BUDGET_HUMAN={limit:async()=>({success:true})};
  let unavailable=false;
  const handler=createDealroomHandler({...identityOverrides(TEST_PARTNER_EMAIL),slugForEmailFn:()=>slug,
   mailNeedsPartnerCaller:async (_env,actor,verb,args)=>{
    assert.equal(verb,'read-mail-needs-partner');assert.equal(actor.slug,slug);
    return readMailNeedsPartner({query:async()=>{if(unavailable)throw Error('outage');return {rows:[]};}},actor,args);
   }});
  const url=`https://${PRODUCTION_HOST}/api/v1/intelligence/mail-needs-partner`;
  assert.equal((await handler.fetch(new Request(url),environment,{})).status,401);
  const signed=await login(handler,environment,TEST_PARTNER_EMAIL);
  const cookie=namedCookie(signed,'__Host-dealroom_session');
  const request=(query='',method='GET')=>handler.fetch(new Request(url+query,{method,headers:{cookie}}),environment,{});
  const result=await request();assert.equal(result.status,200);assert.equal((await result.json()).coverage.mailbox,slug);
  for (const query of ['?owner=joe','?limit=0','?limit=2&limit=3','?limit=1.5','?cursor=bad']) assert.equal((await request(query)).status,400);
  assert.equal((await request('','POST')).status,405);
  unavailable=true;assert.equal((await request()).status,503);
 }
});
test('unknown verified session subject cannot select a mailbox', async () => {
 const environment=env();
 environment.VERB_REQUEST_BUDGET_HUMAN={limit:async()=>({success:true})};
 environment.VERB_REQUEST_BUDGET={idFromName:name=>name,get:()=>({fetch:async()=>Response.json({allowed:true})})};
 const handler=createDealroomHandler({...identityOverrides(TEST_PARTNER_EMAIL),actorFromPropsFn:()=>({id:'11111111-1111-4111-8111-111111111111',slug:'unknown',human:true}),
 mailNeedsPartnerCaller:async (_env,actor,_verb,args)=>readMailNeedsPartner({query:async()=>assert.fail('read')},actor,args)});
 const signed=await login(handler,environment,TEST_PARTNER_EMAIL);
 const cookie=namedCookie(signed,'__Host-dealroom_session');
 const result=await handler.fetch(new Request(`https://${PRODUCTION_HOST}/api/v1/intelligence/mail-needs-partner`,{headers:{cookie}}),environment,{});
 assert.equal(result.status,403);
});
