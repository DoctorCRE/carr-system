export const state = v => {
  const result = v.changedAttempt ? 'Changed evidence submitted' : v.findings.length > 1 ? 'Duplicate proposals' : v.first.length === 0 ? 'Signature unread' : 'Frozen signature retained';
  const stage = {Idle: 'Waiting for next scan', Reconcile: 'Reconciling mail', Review: 'Checking signature', Activity: 'Filing mail activity'}[v.mode];
  return `${v.alias === 0 ? 'Mail without store key' : 'Mail with store key'}: ${stage}, ${result.toLowerCase()}`;
};
export const title = 'Signature review during activity retries';
export const step = a => ({Reconcile: 'resolve saved signature source', Review: 'hold changed evidence or submit frozen proposal', Activity: 'file activity or retain its retry', Scan: 'scan another decoder alias', ChangeSnapshot: 'contact or source changes', Restart: 'restart intake'})[a];
export const readerActions = ['Scan', 'ChangeSnapshot', 'Restart'];
