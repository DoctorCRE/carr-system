import test from 'node:test';
import assert from 'node:assert/strict';
import { dateEvidence } from '../src/email-critical-dates.js';
const complete={body_kind:'full',body_completeness:'complete',body_outcome:'complete',body_truncated:false,attachment_count:0};
const quote='The agreed LOI expiration date is 2026-11-01.';
test('only grounded absolute date-only agreements clear the automatic bar',()=>{
  const evidence=(detail=quote,email_context=complete,q=detail)=>dateEvidence({detail,email_context},'loi_expiry',q);
  assert.deepEqual(evidence(),{due_on:'2026-11-01',reason:null});
  for(const context of [{},{...complete,body_truncated:true},{...complete,body_completeness:'unknown'},
    {...complete,body_outcome:'conflict'},{...complete,body_kind:'preview'}])
    assert.equal(evidence(quote,context).reason,'incomplete_source');
  assert.equal(evidence(quote,complete,'The agreed LOI expiration date is 2026-11-02.').reason,'ungrounded_obligation');
  for(const suffix of [' Proposed only.',' This is not agreed.',' Draft.',' Cancelled.',' Corrected below.'])
    assert.equal(evidence(quote+suffix,complete,quote).reason,'agreement_uncertain');
  assert.equal(evidence(quote+' Another date is 2026-11-02.',complete,quote).reason,'contradictory_dates');
  assert.equal(evidence(quote+' At 5pm.',complete,quote).reason,'timezone_review_required');
  for(const text of ['The agreed LOI expiration date is 2026-02-30.', 'LOI expiration next Friday.',
    'The LOI expiration date is 2026-11-01.']) assert.equal(evidence(text).reason,'absolute_agreement_required');
  assert.equal(evidence(quote,{...complete,attachment_conflict:true}).reason,'attachment_review_required');
  assert.equal(evidence(quote,{...complete,attachment_count:null}).reason,'attachment_review_required');
  assert.equal(evidence(quote,{...complete,attachment_count:1}).reason,'attachment_review_required');
});
