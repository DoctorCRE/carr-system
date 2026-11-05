export const title = 'Bulk Home dispositions and exact undo';
export const state = v => v.phase === 'undone' ? `Undone to ${v.current[1]}` : v.current[1];
export const step = action => ({Apply:'snooze or settle selected items',UndoOne:'undo one applied item',UndoAll:'undo the whole batch',Stay:null})[action];
