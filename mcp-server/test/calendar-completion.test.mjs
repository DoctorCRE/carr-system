import test from 'node:test';
import assert from 'node:assert/strict';
import { completedReview, resolvedCalendarRoles, confirmCalendarCompletion } from '../src/calendar-completion.js';
const row = { key:'1234567890abcdef', source_revision:'a'.repeat(64), canonical_event_identity:'1234567890abcdef'+'b'.repeat(48),
  start:'2026-01-01T10:00:00Z', end:'2026-01-01T11:00:00Z', attendees:[{email:'one@example.test'}],attendee_count:1 };
test('confirmation binds to the source revision and complete attendee evidence', () => {
  assert.equal(completedReview(row,row.source_revision).key,row.key);
  for (const changed of [undefined,'c'.repeat(64)]) assert.throws(()=>completedReview(row,changed));
  assert.throws(()=>completedReview({...row,attendee_count:2},row.source_revision));
  for(const flag of ['conflict','time_changed']) assert.throws(()=>completedReview({...row,[flag]:true},row.source_revision));
  assert.throws(()=>completedReview({...row,disposition:'canceled'},row.source_revision));
  assert.throws(()=>completedReview({...row,end:'2999-01-01T11:00:00Z'},row.source_revision));
});
test('canonical participant resolution never guesses and retains multiple roles', () => {
  const contact = (ref, party='p')=>({address:'one@example.test',ref,exact:true,contact_snapshot:{party_id:party}});
  assert.deepEqual(resolvedCalendarRoles(row,[contact('c'),contact('v')]).refs,['c','v']);
  assert.deepEqual(resolvedCalendarRoles(row,[contact('c'),contact('v','q')]).refs,[]);
  assert.deepEqual(resolvedCalendarRoles(row,[{...contact('c'),exact:false}]).refs,[]);
  assert.deepEqual(resolvedCalendarRoles(row,[]).refs,[]);
});

test('cache lifecycle cannot authorize automatic application', async () => {
  let writes=0;
  await assert.rejects(confirmCalendarCompletion({query:()=>{writes++;}}, {human:false}, row.key,row.source_revision,()=>{writes++;}));
  assert.equal(writes,0);
});
