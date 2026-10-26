import test from 'node:test';
import assert from 'node:assert/strict';
import { splitDealNextStep, validateDealItem, shortDealHeading } from '../src/deal-items.ts';
import { TOOLS } from '../src/tools.js';

test('legacy package splits into three short items while preserving every sourced fact', () => {
  const text = 'Vendor introductions: lender + healthcare attorney intros; Prelim market study: purchase sweep ~5 mi of Synthetic Location Alpha, 8-10 exam rooms, ~$1M all in; walkthrough call before touring Wed/Thu/Fri; ETL sent 11 Sep via CONGA awaiting signature; raise on the package call rather than chasing separately.';
  const items = splitDealNextStep(text, '2026-10-23', 'next-step:synthetic');
  assert.deepEqual(items.map(i => i.title), ['Vendor Introductions', 'Prelim Market Study', 'ETL not completed']);
  assert.deepEqual(items.map(i => i.due_on), ['2026-10-23', '2026-10-23', '2026-10-23']);
  const facts = items.flatMap(i => i.bullets.map(b => b.text)).join(' ');
  for (const fact of ['lender + healthcare attorney intros', '~5 mi of Synthetic Location Alpha', '8-10 exam rooms', '~$1M all in', 'walkthrough call before touring Wed/Thu/Fri', '11 Sep via CONGA awaiting signature', 'raise on the package call rather than chasing separately']) assert.ok(facts.includes(fact), fact);
  assert.ok(items.every(i => i.source_ref === 'next-step:synthetic'));
});

test('structured input refuses invalid due dates, long titles and paragraph bullets', () => {
  const valid = { title: 'Vendor Introductions', due_on: null, source_ref: 'call:synthetic', bullets: [{ date: null, kind: 'call', text: 'Two introductions requested.' }] };
  assert.equal(validateDealItem(valid).title, 'Vendor Introductions');
  for (const patch of [{ due_on: '2026-02-30' }, { title: 'one two three four five' }, { bullets: [{ kind: 'call', text: 'A first fact. A second fact.' }] }]) assert.throws(() => validateDealItem({ ...valid, ...patch }));
});

test('generic signature, attorney and completed facts do not invent specialized outstanding work', () => {
  assert.deepEqual(splitDealNextStep('Send lease for signature.').map(i => i.title), ['Send lease for signature']);
  assert.deepEqual(splitDealNextStep('Review attorney invoice.').map(i => i.title), ['Review attorney invoice']);
  const items = splitDealNextStep('ETL signed 2026-10-20; schedule kickoff call.');
  assert.equal(items.some(i => i.title === 'ETL not completed'), false);
  assert.equal(items.length, 2);
});

test('numbered commitments keep exactly three items and every shared promise and section fact', () => {
  const text = 'Three things owed, all promised in writing on 2026-08-08. (1) INTRODUCTIONS, soonest — two lenders to compare, and an attorney for the two-entity structure and insurance payment question. Watch the inbox for introductions. (2) PACKAGE, one to three weeks from 8 Aug — full-market purchase sweep within five miles of Synthetic Location Alpha across every commercial MLS plus calls, drive-bys and off-market inventory, with the data re-verified rather than taken from the listing. Build it around eight to twelve exam rooms at up to $900K all in, include instructive misses, include anything large enough to lease back the excess, and revisit the four buildings already seen. Walk through it on a call before touring, then tour Wednesday, Thursday or Friday. (3) ETL — sent 8 Aug via CONGA, awaiting signature. Do not chase it separately; raise it on the package-review call if it has not come back.';
  const items = splitDealNextStep(text,'2026-08-29','next-step:synthetic-numbered');
  assert.deepEqual(items.map(i => i.title),['Vendor Introductions','Prelim Market Study','ETL not completed']);
  assert.ok(items.every(i => i.bullets.some(b => b.kind === 'promised' && b.date === '2026-08-08')));
  assert.ok(items[1].bullets.some(b => b.text.includes('off-market inventory')));
  assert.ok(items[2].bullets.some(b => b.text.includes('raise it on the package-review call')));
  assert.equal(items[2].bullets.find(b => b.text.includes('sent 8 Aug via CONGA')).date,'2026-08-08');
  const sourceWords = text.replace(/\([123]\)\s*(?:INTRODUCTIONS|PACKAGE|ETL)/g,'').toLowerCase().match(/[a-z0-9]+/g);
  const detailWords = new Set(items.flatMap(i => i.bullets.map(b => b.text)).join(' ').toLowerCase().match(/[a-z0-9]+/g));
  assert.ok(sourceWords.every(w => detailWords.has(w)),'all source words remain in detail facts');
  assert.ok(items.flatMap(i => i.bullets).every(b => b.text.length <= 240));
});

test('natural dates without a source year remain unassigned', () => {
  assert.equal(splitDealNextStep('ETL sent 8 Aug via CONGA awaiting signature.')[0].bullets[0].date,null);
});

test('numbered headings cannot invent outstanding work or produce invalid one-word titles', () => {
  const items = splitDealNextStep('(1) PACKAGE, send closing documents to counsel. (2) ETL — signed 2026-10-20; schedule kickoff call.');
  assert.deepEqual(items.map(i => i.title),['send closing documents to','signed 2026-10-20']);
  const generic = splitDealNextStep('(1) LEGAL, ask counsel for comments. (2) FINANCING, compare lender terms.');
  assert.deepEqual(generic.map(i => i.title),['ask counsel for comments','compare lender terms']);
  for (const item of [...items,...generic]) assert.doesNotThrow(() => validateDealItem(item));
});

test('short heading uses an explicit recorded doctor name and strips a practice parenthetical', () => {
  assert.equal(shortDealHeading({ name:'Assignment',client_name:'Dr. Morgan Example (Synthetic Practice)',market:'Baytown' }),"Dr. Example's Baytown Deal");
  assert.equal(shortDealHeading({ name:'Recorded assignment',client_name:'Synthetic Practice',market:'Baytown' }),'Recorded assignment');
});

test('item commands expose record-layer writes without a deal-wide completion or note side effect', () => {
  for (const name of ['record-deal-item', 'backfill-deal-items', 'instruct-deal-item', 'complete-deal-item']) {
    assert.equal(TOOLS[name]?.write, true, name);
    assert.ok(TOOLS[name].inputSchema.required.includes('idempotency_key'));
  }
});
