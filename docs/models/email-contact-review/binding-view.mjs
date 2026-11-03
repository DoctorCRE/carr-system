export const title = 'Signature finding atomic binding';
export const state = v => `${v.phase}, ${v.observed === v.current ? 'current snapshot' : 'changed snapshot'}`;
export const step = a => ({Change: 'contact identity changes', Submit: 'submit observed signature', Refresh: 'observe the current contact', Replay: 'retry the recorded request'})[a];
