export const title = 'Queued ask and assistant answer';
export const state = v => `${({ absent: 'No new ask', pending: 'Ask saving', accepted: 'Ask accepted' })[v.phase]}, ${v.authorized ? 'access active' : 'access revoked'}, ${({ idle: 'answer not yet saved', pending: 'answer saving', committed: 'answer saved' })[v.answer]}`;
export const step = action => ({ Submit: 'Partner submits', Commit: 'Ask and receipt commit',
  Rollback: 'Receipt write fails', Answer: 'Assistant saves answer', AnswerCommit: 'Answer commits',
  AnswerRollback: 'Answer transaction fails', Wait: 'Other writer waits', Revoke: 'Partner access revoked',
  Replay: 'Identical retry', Duplicate: 'Concurrent duplicate retries', Refuse: 'Request refused' })[action];
