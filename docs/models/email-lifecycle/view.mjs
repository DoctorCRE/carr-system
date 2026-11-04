export const title = 'Captured mail lifecycle';
export const state = v => v.state;
export const step = action => ({ Act: 'act on live mail', Observe: 'observe terminal state and note earlier actions', Replay: 'replay observation', RefuseStaleOrForeign: null })[action];
