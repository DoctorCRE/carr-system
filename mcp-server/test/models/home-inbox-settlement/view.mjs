export const title = 'Home inbox settlement';
export const state = v => v.disposition !== 'inbox' ? v.disposition === 'snoozed' ? 'Snoozed' : 'Settled manually' : v.override ? 'Explicitly returned to Inbox' : v.source === 'done' ? 'Auto-settled' : v.source === 'working' ? 'Working' : 'Open';
export const step = action => ({ Start: 'work starts', Resolve: 'canonical work resolves', Reopen: 'canonical work reopens', Snooze: 'snooze', Expire: 'snooze expires', Settle: 'settle', Unsettle: 'undo settlement or snooze', Stay: null })[action];
