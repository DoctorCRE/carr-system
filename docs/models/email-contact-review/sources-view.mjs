export const state = v => v.changedAttempt ? 'Changed sender evidence submitted' : v.findings.length > 1 ? 'Duplicate signature proposals' : v.first === 'none' ? 'Signature unread' : 'Frozen signature retained';
export const title = 'Signature review during activity retries';
export const step = a => ({Reconcile: 'resolve saved source independently', Review: 'hold changed evidence or submit frozen proposal', Activity: 'file activity or retain its retry', Scan: 'scan another decoder alias', ChangeSnapshot: 'contact or source changes', Restart: 'restart intake'})[a];
