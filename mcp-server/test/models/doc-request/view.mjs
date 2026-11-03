export const title = 'Queued ask submission';
export const state = v => ({
  absent: v.authorized ? 'No stored ask' : 'Access revoked, no stored ask',
  pending: 'Ask inserted, transaction uncommitted',
  accepted: v.authorized ? 'Ask and receipt committed' : 'Ask retained, access revoked',
})[v.phase];
export const step = action => ({ Submit: 'Partner submits', Commit: 'Receipt commits',
  Rollback: 'Receipt write fails', Revoke: 'Partner access revoked', Replay: 'Identical retry',
  Duplicate: 'Concurrent duplicate retries', Refuse: 'Request refused' })[action];
