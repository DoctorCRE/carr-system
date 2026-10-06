import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { TOOLS } from "../src/tools.js";
import { createLiveClient } from "../../dealroom/js/live-client.js";
import { createPostCallClient, loopbackAllowed } from "../../dealroom/js/post-call-client.js";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const file = (path) => readFile(ROOT + path, "utf8");

function rpcResponse(payload, isError = false) {
  return new Response(JSON.stringify({ jsonrpc: "2.0", id: 1,
    result: { content: [{ type: "text", text: JSON.stringify(payload) }], isError } }),
  { headers: { "content-type": "application/json" } });
}

test("live client derives Dell identity, normalizes conflict shape, and creates as Dell", async () => {
  const calls = [];
  const fetchImpl = async (path, init) => {
    assert.equal(path, "/mcp");
    const request = JSON.parse(init.body);
    calls.push(request.params);
    const name = request.params.name;
    if (name === "deal-room-board") return rpcResponse({ actor: "dell", deals: [{
      id: "d1", name: "Market Deal", phase: "research", type: "startup",
      account_client_id: null,
    }], accounts: [] });
    if (name === "patch-deal-field") return rpcResponse({ ok: false, conflict: {
      id: "c1", deal_id: "d1", field: "owner", value_a: "joe", actor_a: "joe",
      value_b: "dell", actor_b: "dell",
    } });
    if (name === "new-deal") return rpcResponse({ ok: true, deal_id: "d2" });
    if (name === "set-lead") return rpcResponse({ ok: true, new_lead: "dell" });
    throw new Error(`unexpected ${name}`);
  };
  const client = createLiveClient({ fetchImpl });
  const home = await client.getBoard();
  assert.equal(home.actor, "dell");
  assert.equal(client.selfActor, "dell");

  const conflict = await client.patchDealField({ deal: "d1", field: "owner",
    value: "dell", base_event_id: null });
  assert.equal(conflict.status, "conflict");
  assert.deepEqual(conflict.conflict.a, { actor: "joe", value: "joe" });
  assert.deepEqual(conflict.conflict.b, { actor: "dell", value: "dell" });

  await client.createDeal({ client: "C-127", name: "New market", deal_type: "startup",
    phase: "On Deck", lane: "territory" });
  const lead = calls.find((call) => call.name === "set-lead");
  assert.equal(lead.arguments.new_lead, "dell");
  assert.equal(lead.arguments.deal, "d2");
});

test("V5-A01 live client reads assurance health through the registered record-layer verb", async () => {
  const expected = {
    schema_version: "assurance-health.v1",
    scope: { workflow_key: "doctorcre.release", workflow_version: 7, work_request_id: "WR-700" },
    state: "not-yet-operational",
    green: false,
    capability_stage: "draft",
    evidence: {},
  };
  const calls = [];
  const client = createLiveClient({ fetchImpl: async (path, init) => {
    assert.equal(path, "/mcp");
    const request = JSON.parse(init.body);
    calls.push(request.params);
    return rpcResponse(expected);
  } });

  assert.deepEqual(await client.getHealth(expected.scope), expected);
  assert.deepEqual(calls, [{
    name: "read-assurance-health",
    arguments: { scope: expected.scope },
  }]);
});

test("release 1–5 verbs are registered with human gates on structural creation", () => {
  for (const name of ["start-deal-review","review-deal","end-deal-review",
    "set-market-agent","set-national-account-owner","create-national-account",
    "create-national-market-deal","revert-deal-field"]) {
    assert.ok(TOOLS[name], `${name} must be registered`);
  }
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): dead since
  // executeRegisteredTool stopped reading it 2026-08-26 (decision dc57f62d);
  // this slice drops the stale declaration from tools.js. Both verbs remain
  // registered structural-creation writes.
  assert.equal(TOOLS["create-national-account"].humanOnly, undefined);
  assert.equal(TOOLS["create-national-market-deal"].humanOnly, undefined);
});

// The migration names the account it assigns; this public test must not
// (WR-000049), and must not pin it by a plain digest of the name either: an
// unkeyed hash of a name is confirmable by anyone with a name dictionary. It
// pins the whole applied migration file instead, which fixes the literal
// without deriving anything from the name alone.
const MIGRATION_0090_SHA256 = "6599fdf05e4e140bb0ea354993c1904d73a5dc0dc2f2bb197d32feb1c60df17d";

test("national-account migration keeps the 0061 hierarchy and explicitly assigns the national account", async () => {
  const sql = await file("migrations/0090_deal_room_workspaces.sql");
  assert.match(sql, /left join v_client_account vca/);
  const assigned = sql.match(/lower\(p\.name\) = '([^']+)'/);
  assert.ok(assigned, "the migration assigns an account by exact lowercased name");
  assert.equal(createHash("sha256").update(sql).digest("hex"), MIGRATION_0090_SHA256);
  assert.match(sql, /join actor a on a\.slug = 'dell'/);
  assert.match(sql, /create table deal_market_assignment/);
  assert.match(sql, /create table deal_review_session/);
  assert.match(sql, /case when vca\.account_client_id is null then 'team' else 'national_account'/);
  assert.doesNotMatch(sql, /update deal set client_id/i,
    "workspace migration must never reparent or duplicate an existing deal");
});

test("UI exposes two workspaces, explicit controls, mobile cards, and an inactive Calls entrypoint", async () => {
  const [html, app, css, sw] = await Promise.all([
    file("dealroom/index.html"), file("dealroom/js/app.js"),
    file("dealroom/css/app.css"), file("dealroom/public-shell/sw.js"),
  ]);
  assert.match(html, /data-workspace="team"/);
  assert.match(html, /data-workspace="national_account"/);
  assert.match(html, /Review deals/);
  assert.doesNotMatch(html, /Start agenda|id="callModeHeroButton"/);
  // Active Calls are excluded from this release (V5-J101). The entrypoint stays
  // in the toolbar and SAYS so; the recorder controls are gone from the markup
  // rather than hidden behind a flag that something could turn back on.
  assert.match(html, /id="callsButton"/);
  assert.doesNotMatch(html, /id="callModeButton"|id="callModeStop"|data-call-mode-start|id="callModeConsent"/);
  assert.doesNotMatch(app, /callModeHeroButton/);
  assert.match(html, /Search work records/);
  assert.match(app, /new Date\(new Date\(\)\.toDateString\(\)\)/);
  assert.doesNotMatch(app, /new Date\('2026-/);
  assert.match(app, /<select class="cell-select" data-phase/);
  assert.match(app, /<select class="cell-select" data-owner/);
  assert.match(css, /@media\(max-width:680px\)/);
  assert.match(css, /min-height:44px/);
  // The cache name must be VERSIONED — the activate handler purges every other
  // key, so bumping it is how a bad cache gets cleared from installed apps. The
  // exact number is not the contract and pinning it here only made this test
  // fail every time the worker was legitimately corrected.
  assert.match(sw, /dealroom-shell-v\d+/);
  assert.match(sw, /Deliberately no Cache API fallback/);
});

test("parking separates Salesforce record existence from active work", async () => {
  const [sql, hardening, html, app, tools] = await Promise.all([
    file("migrations/0092_deal_operating_state.sql"),
    file("migrations/0093_deal_parking_shape_hardening.sql"), file("dealroom/index.html"),
    file("dealroom/js/app.js"), file("mcp-server/src/tools.js"),
  ]);
  assert.match(sql, /operating_state text not null default 'active'/);
  assert.match(sql, /prospect_never_active.*client_paused.*other/s);
  assert.match(sql, /count\(d\.id\).*operating_state = 'active'.*as open_deals/s);
  assert.doesNotMatch(sql, /update deal set (phase|outcome)/i);
  assert.match(hardening, /parking_reason is not null/);
  assert.match(hardening, /parking_reason in \('prospect_never_active','client_paused','other'\)/);
  assert.match(html, /data-filter="active"[^>]*>Active/);
  assert.match(html, /data-filter="parked"[^>]*>Parked/);
  assert.match(app, /No active work in this agenda/);
  assert.match(app, /Salesforce link, phase, history, and participants stay intact/);
  assert.match(tools, /"operating_state"/);
  assert.match(tools, /dealroom:apply-operating-state/);
  assert.match(tools, /parked_by=case when \$2='parked' then \$5::uuid else null end/);
});

test("Deal Room keeps accessibility and add-work controls available", async () => {
  const [html, app, css] = await Promise.all([
    file("dealroom/index.html"), file("dealroom/js/app.js"), file("dealroom/css/app.css"),
  ]);
  assert.match(html, /id="colorAssistButton"/);
  assert.match(html, /class="board-sticky"[\s\S]*class="filters"[\s\S]*id="search"[\s\S]*id="stickyAddButton"[^>]*>\+ Add work record[\s\S]*class="sticky-table-header"/);
  assert.doesNotMatch(html, /id="addButton"/);
  assert.doesNotMatch(html, /id="persistentAddButton"/);
  assert.match(app, /dealroom-color-assist/);
  assert.match(app, /\$\('#stickyAddButton'\)\.onclick = openAddForm;/);
  assert.doesNotMatch(app, /\$\('#addButton'\)/);
  assert.doesNotMatch(app, /persistentAddButton/);
  assert.match(app, /stickyAgentHeading/);
  assert.match(html, />Color assist</);
  assert.match(css, /body\.color-assist tr\.attention/);
  assert.match(css, /body\.color-assist tr\.parked/);
  // The colour-blind-friendly view has to survive on more than colour, which is
  // the entire point of it: a pattern fill on the row and a shape cue on the
  // phase and owner pickers. All three shipped and none of them was asserted, so
  // a refactor could have dropped the non-colour half and left the tests green.
  assert.match(css, /repeating-linear-gradient/);
  assert.match(css, /cell-select\[data-phase\]/);
  assert.match(css, /cell-select\[data-owner\]/);
  assert.match(css, /\.board-sticky\{position:sticky/);
  assert.match(css, /\.sticky-add-button\{/);
  assert.doesNotMatch(css, /\.persistent-add-button\{/);
});

// The post-call workflow itself is NOT retired — V5-J101 excludes active
// Calls from the first workspace release, and that is a statement about what
// this shell can start, not about what the record layer and the standalone
// controller already hold. So this test now has two halves: the shell reaches
// none of it, and every library, route and never-send rule it will need again
// is still here, unchanged and still covered.
test("the release shell reaches no recorder, and the post-call libraries and never-send rule are preserved", async () => {
  const [html, app, live, local, css] = await Promise.all([
    file("dealroom/index.html"), file("dealroom/js/app.js"),
    file("dealroom/js/live-client.js"), file("dealroom/js/post-call-client.js"),
    file("dealroom/css/app.css"),
  ]);
  // Half one: the shell. No recorder panel, no loopback address, no call to any
  // of the recorder or post-call routes from this workspace.
  assert.doesNotMatch(html, /id="postCallPanel"|id="callModeDialog"/);
  assert.doesNotMatch(html, /127\.0\.0\.1|localhost/,
    "the shell must not offer a local bridge, even as a link a person has to press");
  assert.doesNotMatch(app, /https?:\/\/(127\.0\.0\.1|localhost)/);
  assert.doesNotMatch(app, /post-call-client\.js/, "the shell does not load the local bridge client");
  assert.doesNotMatch(app, /publishCallContext|createOutlookDraft|resolvePostCallCandidate|getCallContext/,
    "no post-call verb is called from the release shell");
  assert.doesNotMatch(html, /id="[^"\n]*transcript/i);

  // Half two: what a later release will switch back on, untouched.
  assert.match(live, /rpc\('get-call-context', \{ deal_ids \}\)/);
  assert.match(live, /write\('resolve-post-call-candidate'/);
  assert.match(local, /\/api\/call-context/);
  assert.match(local, /\/api\/post-call\?session=/);
  assert.match(local, /\/api\/post-call\/sync/);
  assert.match(local, /approved_content_hash/);
  assert.doesNotMatch(app + local, /sendMail|\/send\b|Send email/);
  assert.match(css, /post-call-card/);
  assert.match(css, /@media\(max-width:680px\).*post-call/s);
});

test("post-call client keeps context and Outlook draft creation on narrow loopback routes", async () => {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, init });
    if (url.endsWith('/api/call-context')) return new Response(JSON.stringify({ ok:true, session:'s1' }));
    if (url.includes('/api/post-call?')) return new Response(JSON.stringify({ status:{ state:'review_ready' }, report:{} }));
    if (url.endsWith('/api/post-call/sync')) return new Response(JSON.stringify({ status:{ state:'review_ready' }, report:{} }));
    if (url.endsWith('/api/post-call/drafts/d1/create')) return new Response(JSON.stringify({ draft_id:'d1', idempotent:false }));
    return new Response(JSON.stringify({ error:'not_found' }), { status:404 });
  };
  const client = createPostCallClient({ fetchImpl, pageHostname: '127.0.0.1' });
  await client.publishCallContext({ session:'s1', workspace_kind:'team', generated_at:'now', deals:[] });
  await client.getStatus('s1');
  await client.syncStatus('s1');
  await client.createOutlookDraft('s1', 'd1', 'abc123');
  assert.equal(calls[0].url, 'http://127.0.0.1:4682/api/call-context');
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    session:'s1', workspace_kind:'team', generated_at:'now', deals:[],
  });
  assert.equal(calls[1].url, 'http://127.0.0.1:4682/api/post-call?session=s1');
  assert.deepEqual(JSON.parse(calls[2].init.body), { session:'s1' });
  assert.deepEqual(JSON.parse(calls[3].init.body), { session:'s1', approved_content_hash:'abc123' });
});

test("post-call client never attempts a loopback fetch from a hosted or unknown page", async () => {
  for (const pageHostname of ["app.doctorcre.com", "dealroom.doctorcre.com", "localhost.evil.example", "", undefined]) {
    const calls = [];
    const client = createPostCallClient({ fetchImpl: async (url) => { calls.push(url); return new Response("{}"); }, pageHostname });
    await assert.rejects(client.getStatus("s1"), (error) => error.code === "loopback_not_permitted");
    await assert.rejects(client.publishCallContext({ session: "s1" }), (error) => error.code === "loopback_not_permitted");
    assert.deepEqual(calls, [], `${pageHostname} must not reach the loopback processor`);
  }
  for (const host of ["localhost", "127.0.0.1", "LOCALHOST", "[::1]"]) assert.equal(loopbackAllowed(host), true, host);
  assert.equal(loopbackAllowed(undefined), false, "no location fails closed");
});
