export const state = v => `Queue ${v.cursor === 2 ? 'at end' : 'advancing'}, alerts ${v.emitted.length ? v.emitted.join(', ') : 'none'}, head ${v.unlocked ? 'unlocked' : 'locked'}`;
export const step = a => ({Attempt:'Try the next waiting watch',Wrap:'Start the next queue pass',Unlock:'The held transaction finishes'}[a]??a);
export const readerActions=[];
