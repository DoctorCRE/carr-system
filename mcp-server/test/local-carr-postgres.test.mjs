// The local CARR harness the app's live journeys run against: the real Worker,
// signed in through the staging e2e door, over a disposable migrated database,
// with a call filed through the real Call Mode path.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startLocalCarr } from './helpers/local-carr.mjs';

let carr, cookie;
before(async () => {
  carr = await startLocalCarr();
  console.log(`local-carr setup ${carr.setupMs}ms`);
});
after(() => carr?.close());

// A verb as the signed-in browser partner, through the cookie door.
async function asPartner(name, args = {}) {
  const response = await fetch(`${carr.url}/mcp`, { method: 'POST', headers: { cookie, origin: carr.url, 'sec-fetch-site': 'same-origin',
    'content-type': 'application/json', accept: 'application/json, text/event-stream' },
  body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } }) });
  assert.equal(response.status, 200);
  return JSON.parse((await response.json()).result.content[0].text);
}

test('a browser signs in as the synthetic partner and reads the Demo practice book', async () => {
  const refused = await fetch(`${carr.url}/auth/e2e-session`, { method: 'POST', headers: { authorization: 'Bearer wrong' } });
  assert.equal(refused.status, 401);
  const signIn = await fetch(`${carr.url}/auth/e2e-session`, { method: 'POST', headers: { authorization: `Bearer ${carr.e2eSecret}` } });
  assert.equal(signIn.status, 200);
  cookie = signIn.headers.getSetCookie().map(value => value.split(';', 1)[0]).join('; ');
  const session = await (await fetch(`${carr.url}/auth/session`, { headers: { cookie } })).json();
  assert.equal(session.actor.slug, 'joe');
  assert.equal(session.e2e_principal, 'e2e-joe');
  const agenda = await asPartner('read-home-agenda');
  assert.deepEqual(agenda.deals.map(deal => deal.name).sort(), Object.keys(carr.deals).sort());
});

test('a call filed through Call Mode reaches the signed-in partner and the deal', async () => {
  const bayside = carr.deals['Synthetic Bayside Dental Relocation'];
  await carr.fileCall();
  const { calls } = await asPartner('read-call-outcomes');
  assert.equal(calls.length, 1);
  assert.ok(calls[0].applied.some(change => change.deal_id === bayside && change.kind === 'phase_move'), JSON.stringify(calls));
  const { candidates } = await asPartner('read-post-call-followups');
  assert.ok(candidates.some(row => row.kind === 'assigned_action' && row.payload.action === 'Call the Harbor lender'), JSON.stringify(candidates));
  const room = await asPartner('get-deal-room', { deal: bayside });
  assert.equal(room.phase, 'negotiation');
  assert.match(room.next_step, /lease draft/i);
});

test('a call whose extraction fails stays blocked and files nothing', async () => {
  const call = await carr.fileCall({ distiller: 'failing' });
  const review = await call.request(`/api/post-call?session=${encodeURIComponent(call.name)}`);
  assert.equal(review.status.state, 'blocked');
  assert.equal(review.status.reason, 'local post-call distiller failed');
  assert.equal((await asPartner('read-call-outcomes')).calls.length, 1, 'only the earlier call is filed');
});
