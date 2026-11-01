// P1 S4 proof (docs/memory/team-memory-design.md §9): the staged convention
// proposals, governance-queue's proposal lane, adopt-rule's refusals and
// adoption by partners and their sponsored agents, against a disposable
// PostgreSQL built as deploy builds it: db/schema.sql, then every migration
// missing from its ledger.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { migrationNames } from './migration-source.testhelper.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';

const REPO = new URL('../../', import.meta.url).pathname;
const MIGRATION = migrationNames().find(name => /^\d{4}_rule_card_adoption\.sql$/.test(name));
const PLAN = JSON.parse(execFileSync('python3', [`${REPO}ops/stage-convention-proposals.py`, '--print'],
  { encoding: 'utf8' }));

// The connector Claude session and the local machine door, both sponsored by
// Joe and both holding partner authority for ordinary humanOnly verbs.
const claudeForJoe = { slug: 'claude', human: false,
  sponsoring_human_slug: 'joe', native_agent_verified: true, via: 'oauth-agent', client_id: 'claude-client' };
const joeLocal = { slug: 'joe-local', human: false,
  sponsoring_human_slug: 'joe', native_agent_verified: true, via: 'local-token', client_id: 'local-verb' };

let fixture, c, joe, dell;
before(async () => {
  assert.ok(MIGRATION, 'the S4 migration exists');
  fixture = await acquireDisposablePostgres({ prefix: 'org-conventions-', snapshot: true });
  c = new pg.Client(fixture.connection);
  await c.connect();

  await c.query(`do $roles$ begin
    if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
    if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then create role carr_authority_dell login; end if;
    grant carr_authority to carr_authority_joe; grant carr_authority to carr_authority_dell;
  end $roles$`);
  const partner = async slug => ({ ...(await c.query(`insert into actor(slug,kind,display_name) values($1,'human','Synthetic partner')
    on conflict(slug) do update set active=true returning id,slug`, [slug])).rows[0], human: true,
    via: 'dealroom-cookie', client_id: 'dealroom-pwa' });
  joe = await partner('joe');
  dell = await partner('dell');
  // The dispatcher refuses an unprovisioned actor before any handler runs.
  for (const agent of [claudeForJoe, joeLocal])
    agent.id = (await c.query(`insert into actor(slug,kind,display_name) values($1,'automation','Synthetic agent')
      on conflict(slug) do update set active=true returning id`, [agent.slug])).rows[0].id;
  await seedDeliveredRule();
});

// The empty fixture has no rule packs and no active rules, which the
// production policy-integrity check refuses at commit. Seed one active,
// pack-delivered rule through the real approval chain, as production holds.
const PACK_INDEX = ['client-deal', 'delegation-council', 'engineering-git', 'governance-rules', 'joe-comms',
  'joe-development', 'records-intake', 'scheduled-automation', 'source-study', 'surface-doctrine', 'vendor-intros'];
async function seedDeliveredRule() {
  await c.query('begin');
  // The production pack index; adopt_rule refuses any other name.
  for (const pack of PACK_INDEX)
    await c.query(`insert into ops.rule_pack(pack,title,description,triggers,source)
      values($1,$1,'Synthetic copy of the production pack','{x}','org-conventions-postgres') on conflict (pack) do nothing`,
      [pack]);
  const id = (await c.query(`insert into rule(statement,taught_by,status) values('Synthetic delivered rule.',$1,'proposed')
    returning id`, [joe.id])).rows[0].id;
  await c.query(`insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
    values($1::uuid,left($1::text,8),'pack',$2,'shared',null,'org-conventions-postgres',repeat('0',64))`, [id, PACK_INDEX]);
  await c.query('set local session authorization carr_authority_joe');
  await c.query(`select ops.approve_rule($1,'human_only',array['human_authority_runtime'],$2,'Synthetic fixture rule')`,
    [id, randomUUID()]);
  await c.query('commit');
}
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

// `as` is the database principal the deployed dispatcher would connect as:
// carr_writer for ordinary writes, the partner's authority login for adopt-rule.
async function call(actor, name, args, as = 'carr_writer') {
  await c.query('begin');
  try {
    await c.query(as === 'carr_writer' ? 'set local role carr_writer' : `set local session authorization ${as}`);
    const keyed = ['governance-queue', 'rule-cards', 'find-rule'].includes(name) ? args : { idempotency_key: randomUUID(), ...args };
    const result = await executeRegisteredTool(c, actor, name, keyed);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}

async function refused(promise, code) {
  await assert.rejects(promise, error => {
    assert.equal(error.payload?.error, code, error.message);
    assert.equal(publicError(error).error, code);
    return true;
  });
}

const ruleCount = async () => Number((await c.query('select count(*) from rule')).rows[0].count);
const adopt = (actor, proposal, as, extra = {}) => call(actor, 'adopt-rule', { section_id: proposal.section_id,
  base_version: proposal.base_version, reason: 'Synthetic adoption', ...extra }, as);

let queue;
test('the staging plan writes every proposal through the team-memory gates, and no proposal touches rule', async () => {
  const before = await ruleCount();
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'claude_code' });
  for (const p of PLAN)
    await call(joe, 'write-doctrine-section', { document: 'team-memory', section_key: p.section_key,
      title: p.title, body_text: p.body_text, base_version: 0, attach_id });
  assert.equal(await ruleCount(), before, 'a proposal section cannot change rule');
  queue = (await call(joe, 'governance-queue', {})).pending_convention_proposals;
  assert.equal(queue.length, PLAN.length);
  assert.ok(queue.every(p => p.adoptable), 'every staged proposal is adoptable');
});

const proposal = key => queue.find(p => p.section_key === `conventions/proposed/${key}`);

test('adopt-rule refuses the unattended runner', async () => {
  await refused(adopt({ ...joe, unattended: true }, proposal('card-k01'), 'carr_authority_joe'),
    'unattended_runner_authority_not_admitted');
  assert.equal((await c.query('select count(*) from rule where card is not null')).rows[0].count, '0');
});

test('the writer connection cannot reach ops.adopt_rule', async () => {
  await refused(adopt(joe, proposal('card-k01'), 'carr_writer'), 'database_refused_the_statement');
});

test('Joe adopts a core card in person: active rule, card on the rule, proposal retired', async () => {
  const k01 = proposal('card-k01');
  const out = await adopt(joe, k01, 'carr_authority_joe');
  assert.equal(out.card_id, 'K01');
  assert.equal(out.load_layer, 'control', 'adoption never grows the capped layer0 boot set');
  const rule = (await c.query('select * from rule where id=$1', [out.rule_id])).rows[0];
  assert.equal(rule.status, 'active');
  assert.equal(rule.activated_by, joe.id);
  assert.equal(rule.taught_by, joe.id);
  assert.equal(rule.card.id, 'K01');
  assert.equal(rule.card.placement, 'CORE');
  assert.equal(rule.card.wording, undefined);
  assert.equal(rule.statement, k01.statement);
  const section = (await c.query('select status, current_version from doctrine_section where id=$1', [k01.section_id])).rows[0];
  assert.deepEqual(section, { status: 'retired', current_version: String(k01.base_version + 1) });
  const session = (await c.query(`select m.surface from doctrine_change_set cs
    join memory_session m on m.attach_id::text = cs.session_key where cs.idempotency_key like 'adopt-rule:%'`)).rows;
  assert.deepEqual(session, [{ surface: 'app' }], 'the retire is attributed to the approval surface attach');
  const snap = await executeRegisteredTool(c, joe, 'read-doctrine', { document: 'team-memory' });
  assert.equal(snap.generation, out.generation);
  assert.ok(!snap.sections.some(s => s.section_id === k01.section_id), 'the snapshot drops the retired proposal');
  const after = (await call(joe, 'governance-queue', {})).pending_convention_proposals;
  assert.equal(after.length, queue.length - 1);
  const cards = await call(joe, 'rule-cards', {});
  assert.equal(cards.count, 1);
  assert.equal(cards.cards[0].id, 'K01');
  assert.equal(cards.cards[0].wording, k01.statement);
});

test('an adopted card cannot be adopted twice, and a stale base_version is refused', async () => {
  const k01 = proposal('card-k01');
  await refused(adopt(joe, k01, 'carr_authority_joe'), 'convention_proposal_not_found');
  // A staging rerun writes the retired key at base_version 0 and gets
  // version_conflict, which ops/stage-convention-proposals.py skips.
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'claude_code' });
  const staged = PLAN.find(p => p.section_key === k01.section_key);
  await refused(call(joe, 'write-doctrine-section', { document: 'team-memory', section_key: staged.section_key,
    title: staged.title, body_text: staged.body_text, base_version: 0, attach_id }), 'version_conflict');
  const k02 = proposal('card-k02');
  await refused(adopt(joe, { ...k02, base_version: k02.base_version + 1 }, 'carr_authority_joe'), 'version_conflict');
});

test('a deal convention is delivered to a session that declares client-deal, and not without it', async () => {
  const territory = proposal('team-territory');
  assert.deepEqual(territory.packs, ['client-deal', 'source-study']);
  const out = await adopt(joe, territory, 'carr_authority_joe');
  assert.equal(out.card_id, null);
  assert.equal(out.load_layer, 'pack');
  assert.deepEqual(out.packs, ['client-deal', 'source-study']);
  const rule = (await c.query('select status, card from rule where id=$1', [out.rule_id])).rows[0];
  assert.deepEqual(rule, { status: 'active', card: null });
  const plan = async packs => (await c.query('select selected from ops.rule_delivery_plan($1,$2) where rule_id=$3',
    ['joe', packs, out.rule_id])).rows[0]?.selected;
  assert.equal(await plan(['client-deal']), true, 'the scoped boot hands it over with the deal pack');
  assert.equal(await plan([]), false, 'it is pack-delivered, not layer0');
  assert.equal(await plan(['engineering-git']), false, 'repo work does not carry a deal convention');
  // The session-side read: standing-context with the pack the session declares.
  const shortId = out.rule_id.slice(0, 8);
  const context = async packs => executeRegisteredTool(c, claudeForJoe, 'standing-context', { packs });
  const deal = await context(['client-deal']);
  assert.deepEqual(deal.rule_delivery?.packs_not_found ?? [], []);
  assert.ok(deal.shared_rules.some(r => r.id === shortId), 'a deal session is handed the convention');
  assert.ok(!deal.rule_delivery.would_omit.includes(shortId), 'the scoped selector keeps it');
  assert.equal(deal.rule_delivery.pack_index.find(p => p.pack === 'client-deal').rules, 2);
  for (const packs of [[], ['engineering-git']])
    assert.ok((await context(packs)).rule_delivery.would_omit.includes(shortId),
      `without client-deal (packs ${JSON.stringify(packs)}) the scoped selector drops it`);
});

test('a card-less proposal must name existing packs: no Packs line or an unknown pack is refused', async () => {
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'claude_code' });
  const write = (key, body_text) => call(joe, 'write-doctrine-section', { document: 'team-memory',
    section_key: `conventions/proposed/${key}`, body_text, base_version: 0, attach_id });
  const packless = await write('packless', 'Convention: Synthetic packless convention.\nSource: test');
  const unknown = await write('unknown-pack', 'Convention: Synthetic convention.\nPacks: client-deal, deals-everywhere');
  const listed = (await call(joe, 'governance-queue', {})).pending_convention_proposals;
  assert.equal(listed.find(p => p.section_id === packless.section_id).adoptable, false);
  const before = await ruleCount();
  await refused(adopt(joe, { section_id: packless.section_id, base_version: 1 }, 'carr_authority_joe'),
    'convention_proposal_malformed');
  await refused(adopt(joe, { section_id: unknown.section_id, base_version: 1 }, 'carr_authority_joe'),
    'convention_pack_unknown');
  assert.equal(await ruleCount(), before);
  assert.equal((await c.query(`select count(*) from ops.rule_pack where pack='deals-everywhere'`)).rows[0].count, '0');
});

test('two proposals with one card id adopted concurrently: the second gets the named refusal', async () => {
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'claude_code' });
  const k03 = proposal('card-k03');
  const twinText = (await c.query(`select r.body->>'text' as t from doctrine_section s
    join doctrine_revision r on r.id=s.current_revision_id where s.id=$1`, [k03.section_id])).rows[0].t;
  const twin = await call(joe, 'write-doctrine-section', { document: 'team-memory',
    section_key: 'conventions/proposed/card-k03-twin', body_text: twinText, base_version: 0, attach_id });
  const second = new pg.Client(fixture.connection);
  await second.connect();
  try {
    await c.query('begin');
    await c.query('set local session authorization carr_authority_joe');
    await executeRegisteredTool(c, joe, 'adopt-rule', { idempotency_key: randomUUID(), section_id: k03.section_id,
      base_version: k03.base_version, reason: 'First of two' });
    await second.query('begin');
    await second.query('set local session authorization carr_authority_joe');
    const racing = refused(executeRegisteredTool(second, joe, 'adopt-rule', { idempotency_key: randomUUID(),
      section_id: twin.section_id, base_version: 1, reason: 'Second of two' }), 'convention_card_already_active');
    const deadline = performance.now() + 60000;
    while (!(await c.query(`select cardinality(pg_blocking_pids($1)) > 0 as blocked`,
      [second.processID])).rows[0].blocked) {
      assert.ok(performance.now() < deadline, 'second adoption must wait for the first transaction');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    await c.query('commit');
    await racing;
    await second.query('rollback');
    await refused(adopt(joe, { section_id: twin.section_id, base_version: 1 }, 'carr_authority_joe'),
      'convention_card_already_active');
  } finally { await c.query('rollback'); await second.end(); }
  assert.equal((await c.query(`select count(*) from rule where status='active' and card->>'id'='K03'`)).rows[0].count, '1');
});

test('a malformed proposal is refused and changes nothing', async () => {
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'claude_code' });
  const written = await call(joe, 'write-doctrine-section', { document: 'team-memory',
    section_key: 'conventions/proposed/free-prose', body_text: 'Proposed convention: free prose', base_version: 0, attach_id });
  const before = await ruleCount();
  await refused(adopt(joe, { section_id: written.section_id, base_version: 1 }, 'carr_authority_joe'),
    'convention_proposal_malformed');
  assert.equal(await ruleCount(), before);
});

test('Dell\'s authority login is refused by the Joe-only approval chain (named gap)', async () => {
  await refused(adopt(dell, proposal('card-k02'), 'carr_authority_dell'), 'rule_adoption_requires_joe');
});

async function proposeText(slug, body_text) {
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'codex', access: 'read_write' });
  const written = await call(joe, 'write-doctrine-section', { document: 'team-memory',
    section_key: `conventions/proposed/${slug}`, body_text, base_version: 0, attach_id });
  return { section_id: written.section_id, base_version: 1 };
}

async function adoptCard(slug, statement = 'Synthetic obsolete instruction.') {
  const p = await proposeText(slug, `Convention: ${statement}\nCard: {"id":"${slug}","placement":"CORE"}`);
  return adopt(joe, p, 'carr_authority_joe');
}

test('retirement-only adoption records who, why and the proposal without creating a rule', async () => {
  const old = await adoptCard('retirement-only');
  const p = await proposeText('retire-only', `Retires: ["${old.rule_id}"]\nWhy: Obsolete source.`);
  const before = await ruleCount();
  const out = await adopt(joe, p, 'carr_authority_joe', { reason: 'Use the current source.' });
  assert.equal(out.rule_id, null);
  assert.deepEqual(out.retired_rule_ids, [old.rule_id]);
  assert.equal(await ruleCount(), before);
  const found = (await call(joe, 'find-rule', { text: 'Synthetic obsolete instruction.' })).rules.find(r => r.id === old.rule_id);
  assert.equal(found.status, 'retired');
  assert.equal(found.retirement.retired_by, 'joe');
  assert.match(found.retirement.reason, /Use the current source\./);
  assert.ok(found.retirement.reason.includes(`${p.section_id}@1`));
  const cards = await call(joe, 'rule-cards', {});
  assert.ok(!cards.cards.some(card => card.rule_id === old.rule_id));
  const pending = (await call(joe, 'governance-queue', {})).pending_convention_proposals;
  assert.ok(!pending.some(row => row.section_id === p.section_id));
});

test('the governance queue describes retirement targets and optional replacement', async () => {
  const old = await adoptCard('queue-retirement', 'Synthetic calendar instruction.');
  const p = await proposeText('queue-retire', `Retires: ["${old.rule_id}"]`);
  const row = (await call(joe, 'governance-queue', {})).pending_convention_proposals.find(r => r.section_id === p.section_id);
  assert.equal(row.adoptable, true);
  assert.deepEqual(row.retires, [old.rule_id]);
  assert.equal(row.statement, `retires rule ${old.rule_id}: Synthetic calendar instruction.`);
  assert.equal(row.replacement_statement, null);
  assert.deepEqual(row.packs, []);
});

// Joe, 2026-10-31: "If you think a rule is needed you just make it a rule and
// tell me you did it." The event row names the agent that adopted.
test('Joe-sponsored agents and connector sessions adopt and retire rules themselves', async () => {
  const connector = { ...joe, via: 'oauth-google', client_id: 'claude-ai-connector' };
  for (const [i, actor] of [claudeForJoe, joeLocal, connector].entries()) {
    const p = await proposeText(`agent-card-${i}`, `Convention: Synthetic agent rule ${i}.\nCard: {"id":"agent-card-${i}","placement":"CORE"}`);
    const made = await adopt(actor, p, 'carr_authority_joe', { reason: 'The agent judged this rule useful.' });
    assert.equal((await c.query('select status from rule where id=$1', [made.rule_id])).rows[0].status, 'active');
    const r = await proposeText(`agent-retire-${i}`, `Retires: ["${made.rule_id}"]`);
    const out = await adopt(actor, r, 'carr_authority_joe', { reason: 'The agent judged this rule obsolete.' });
    assert.deepEqual(out.retired_rule_ids, [made.rule_id]);
    assert.equal((await c.query('select status from rule where id=$1', [made.rule_id])).rows[0].status, 'retired');
    const events = (await c.query(`select actor_id, via from event where verb='adopt-rule' and subject_id in ($1::uuid,$2::uuid)
      order by recorded_at`, [made.rule_id, r.section_id])).rows;
    assert.deepEqual(events, [{ actor_id: actor.id, via: actor.via }, { actor_id: actor.id, via: actor.via }]);
  }
});

test('Dell\'s authority login still cannot retire through the Joe-only chain', async () => {
  const old = await adoptCard('dell-retirement');
  const p = await proposeText('dell-retire', `Retires: ["${old.rule_id}"]`);
  await refused(adopt(dell, p, 'carr_authority_dell'), 'rule_adoption_requires_joe');
  assert.equal((await c.query('select status from rule where id=$1', [old.rule_id])).rows[0].status, 'active');
});

// WR631.S1.J3: the rule-lookup hook reported missing_scope for every rule
// adopt-rule made, because adoption stored scope {}.
test('an adopted convention carries a scope the hook selector accepts', async () => {
  const p = await proposeText('scoped-convention', 'Convention: When a synthetic moment arrives, do the synthetic thing.\nPacks: governance-rules');
  const out = await adopt(claudeForJoe, p, 'carr_authority_joe');
  const rule = (await c.query('select scope from rule where id=$1', [out.rule_id])).rows[0];
  assert.deepEqual(rule.scope, { moment: 'When a synthetic moment arrives, do the synthetic thing.' });
  const catalog = await executeRegisteredTool(c, claudeForJoe, 'standing-context', { detail: 'scopes' });
  const selection = JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from lib.rule_scope_lookup import select_rules; print(json.dumps(select_rules(json.load(sys.stdin), {"tool_name": "Bash", "tool_input": {"command": "true"}})))'],
    { cwd: REPO, input: JSON.stringify(catalog), encoding: 'utf8' }));
  assert.ok(catalog.rules.some(r => r.id === out.rule_id), 'the hook catalog carries the adopted rule');
  assert.deepEqual(selection.data_defects.filter(d => d.rule_id === out.rule_id), []);
});

// A legacy proposed rule closes the same way: the agent adopts its current
// wording as a replacement and the proposal row retires, superseded by it.
test('a proposed rule retires into its adopted replacement', async () => {
  const proposed = (await c.query(`insert into rule(statement,taught_by) values ('Synthetic stale proposal.',$1) returning id`, [joe.id])).rows[0].id;
  const p = await proposeText('replace-proposed', `Convention: Synthetic current proposal.\nPacks: governance-rules\nRetires: ["${proposed}"]`);
  const row = (await call(joe, 'governance-queue', {})).pending_convention_proposals.find(r => r.section_id === p.section_id);
  assert.equal(row.adoptable, true);
  const out = await adopt(joe, p, 'carr_authority_joe');
  assert.deepEqual(out.retired_rule_ids, [proposed]);
  const old = (await call(joe, 'find-rule', { text: 'Synthetic stale proposal.' })).rules.find(r => r.id === proposed);
  assert.equal(old.status, 'retired');
  assert.equal(old.retirement.superseded_by, out.rule_id);
  assert.equal((await c.query('select status from rule where id=$1', [out.rule_id])).rows[0].status, 'active');
});

test('unknown and already-retired targets are refused without partial retirement', async () => {
  const old = await adoptCard('invalid-target-retirement');
  for (const [slug, id, code] of [
    ['unknown', randomUUID(), 'convention_retirement_rule_not_found'],
  ]) {
    const p = await proposeText(`retire-${slug}`, `Retires: ${JSON.stringify([old.rule_id, id])}`);
    const before = await ruleCount();
    await refused(adopt(joe, p, 'carr_authority_joe'), code);
    assert.equal(await ruleCount(), before);
    assert.equal((await c.query('select status from rule where id=$1', [old.rule_id])).rows[0].status, 'active');
    assert.equal((await c.query('select status from doctrine_section where id=$1', [p.section_id])).rows[0].status, 'active');
  }
  const retirement = await proposeText('retire-once', `Retires: ["${old.rule_id}"]`);
  await adopt(joe, retirement, 'carr_authority_joe');
  const again = await proposeText('retire-twice', `Retires: ["${old.rule_id}"]`);
  await refused(adopt(joe, again, 'carr_authority_joe'), 'convention_retirement_rule_not_active');
});

test('replacement and multiple retirements reuse the card id in one adoption', async () => {
  const first = await adoptCard('replacement-card', 'Synthetic first instruction.');
  const second = await adoptCard('replacement-second', 'Synthetic second instruction.');
  const p = await proposeText('replace-and-retire', `Convention: Synthetic replacement instruction.\nCard: {"id":"replacement-card","placement":"CORE"}\nRetires: ${JSON.stringify([second.rule_id, first.rule_id, first.rule_id])}`);
  const out = await adopt(joe, p, 'carr_authority_joe');
  assert.ok(out.rule_id);
  assert.deepEqual(out.retired_rule_ids, [first.rule_id, second.rule_id].sort());
  const retired = (await call(joe, 'find-rule', { text: 'Synthetic', status: 'retired' })).rules.filter(r => out.retired_rule_ids.includes(r.id));
  assert.equal(retired.length, 2);
  assert.ok(retired.every(r => r.retirement.superseded_by === out.rule_id));
  const cards = (await call(joe, 'rule-cards', {})).cards;
  assert.equal(cards.find(card => card.id === 'replacement-card').rule_id, out.rule_id);
  assert.ok(!cards.some(card => card.rule_id === second.rule_id));
});

test('failed replacement approval rolls back retirement, receipt and proposal consumption', async () => {
  const key = randomUUID();
  const previous = await proposeText('approval-key-owner', 'Convention: Synthetic approval key owner.\nPacks: engineering-git');
  await adopt(joe, previous, 'carr_authority_joe', { idempotency_key: key });
  const old = await adoptCard('atomic-retirement');
  const p = await proposeText('atomic-replacement', `Convention: Synthetic atomic replacement.\nPacks: engineering-git\nRetires: ["${old.rule_id}"]`);
  const before = await ruleCount();
  await c.query('begin');
  try {
    await c.query('set local session authorization carr_authority_joe');
    await assert.rejects(c.query('select ops.adopt_rule($1,1,$2,$3)', [p.section_id, key, 'Synthetic reused approval key']), /idempotency|duplicate key/);
  } finally { await c.query('rollback'); }
  assert.equal(await ruleCount(), before);
  assert.equal((await c.query('select status from rule where id=$1', [old.rule_id])).rows[0].status, 'active');
  assert.equal((await c.query('select count(*) from ops.rule_retirement_receipt where rule_id=$1', [old.rule_id])).rows[0].count, '0');
  assert.equal((await c.query('select status from doctrine_section where id=$1', [p.section_id])).rows[0].status, 'active');
  const out = await adopt(joe, p, 'carr_authority_joe');
  assert.ok(out.rule_id);
});

test('malformed retirement lists are non-adoptable and cannot retire rules', async () => {
  const old = await adoptCard('malformed-retirement');
  for (const [index, body] of [
    'Retires: []', 'Retires: null', 'Retires: "not-an-array"', 'Retires: [null]',
    'Retires: ["49533583"]', 'Retires: broken',
    `Retires: ["${old.rule_id}"]\nRetires: ["${old.rule_id}"]`,
    `Retires: ["${old.rule_id}"]\nPacks: engineering-git`,
  ].entries()) {
    const p = await proposeText(`malformed-retire-${index}`, body);
    const row = (await call(joe, 'governance-queue', {})).pending_convention_proposals.find(r => r.section_id === p.section_id);
    assert.equal(row.adoptable, false, body);
    await refused(adopt(joe, p, 'carr_authority_joe'), 'convention_proposal_malformed');
  }
  assert.equal((await c.query('select status from rule where id=$1', [old.rule_id])).rows[0].status, 'active');
});


test('PR card attribution is not rule-card JSON and unknown packs name the options', async () => {
  const p = await proposeText('pr-attribution', 'Convention: Synthetic attributed rule.\nCard: WR698.S1.J1\nPacks: governance-rules');
  const out = await adopt(joe, p, 'carr_authority_joe');
  assert.ok(out.rule_id);
  await assert.rejects(adopt(joe, p, 'carr_authority_joe'), error => {
    assert.match(publicError(error).hint, /Retires:/);
    return true;
  });
  const bad = await proposeText('pack-options', 'Convention: Synthetic unknown pack.\nPacks: does-not-exist');
  await assert.rejects(adopt(joe, bad, 'carr_authority_joe'), error => {
    const field = publicError(error).field_errors.find(e => e.field === 'Packs');
    assert.deepEqual(field.value, ['does-not-exist']);
    assert.ok(field.allowed_values.includes('governance-rules'));
    return true;
  });
});

for (const route of ['tool', 'sql']) {
  const adoptVia = async proposal => {
    if (route === 'tool') return adopt(joe, proposal, 'carr_authority_joe');
    await c.query('begin');
    try {
      await c.query('set local session authorization carr_authority_joe');
      const out = (await c.query('select ops.adopt_rule($1,$2,$3,$4) as adopted',
        [proposal.section_id, proposal.base_version, randomUUID(), 'Synthetic attribution regression'])).rows[0].adopted;
      await c.query('commit');
      return out;
    } catch (error) { await c.query('rollback'); throw error; }
  };

  test(`${route} ignores first-line PR attribution before a packed convention`, async () => {
    const p = await proposeText(`attribution-first-${route}`,
      'Card: WR1.S1.J1\nConvention: Synthetic attributed first line.\nPacks: governance-rules');
    const out = await adoptVia(p);
    assert.ok(out.rule_id);
    assert.equal(out.card_id, null);
    assert.deepEqual(out.packs, ['governance-rules']);
  });

  test(`${route} ignores PR attribution in retirement-only proposals`, async () => {
    for (const first of [false, true]) {
      const old = await adoptCard(`attribution-retire-${route}-${first}`);
      const lines = [`Retires: ["${old.rule_id}"]`, 'Card: WR1.S1.J1 \t\r'];
      const p = await proposeText(`attribution-retirement-${route}-${first}`, (first ? lines.reverse() : lines).join('\n'));
      const out = await adoptVia(p);
      assert.equal(out.rule_id, null);
      assert.deepEqual(out.retired_rule_ids, [old.rule_id]);
      assert.equal((await c.query('select status from rule where id=$1', [old.rule_id])).rows[0].status, 'retired');
    }
  });

  test(`${route} keeps NBSP-terminated attribution significant`, async () => {
    const old = await adoptCard(`attribution-nbsp-${route}`);
    const p = await proposeText(`attribution-nbsp-proposal-${route}`,
      `Retires: ["${old.rule_id}"]\nCard: WR1.S1.J1\u00a0`);
    const row = (await call(joe, 'governance-queue', {})).pending_convention_proposals
      .find(row => row.section_id === p.section_id);
    assert.equal(row.adoptable, false);
    await assert.rejects(adoptVia(p), /convention_proposal_malformed/);
    assert.equal((await c.query('select status from rule where id=$1', [old.rule_id])).rows[0].status, 'active');
  });

  test(`${route} ignores PR attribution without Packs when JSON card metadata exists`, async () => {
    const p = await proposeText(`attribution-json-${route}`,
      `Card: WR1.S1.J1\nConvention: Synthetic attributed JSON card.\nCard: {"id":"attribution-${route}","placement":"CORE"}\nCard: WR2.S3.J4  `);
    assert.equal((await adoptVia(p)).card_id, `attribution-${route}`);
  });
}

test('PR attribution supplies no delivery metadata and noncanonical Card lines stay invalid', async () => {
  const p = await proposeText('attribution-without-delivery', 'Convention: Synthetic missing delivery.\nCard: WR1.S1.J1');
  await refused(adopt(joe, p, 'carr_authority_joe'), 'convention_proposal_malformed');
  const old = await adoptCard('attribution-not-canonical');
  for (const [index, line] of ['Card: WR1.S1.J1 extra', 'Card: WR1xS1xJ1', 'Card: {"id":"metadata","placement":"CORE"}'].entries()) {
    const bad = await proposeText(`attribution-invalid-${index}`, `Retires: ["${old.rule_id}"]\n${line}`);
    await refused(adopt(joe, bad, 'carr_authority_joe'), 'convention_proposal_malformed');
  }
});
