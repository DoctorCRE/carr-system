export const state = v => v.phase;
export const title = 'Email signature findings';
export const step = action => ({Resolve: 'resolve sender and inspect signature', Submit: 'submit frozen proposal', Retry: 'retry or hold after validation', Replay: 'repeat scan', Done: 'keep disposition', RecoverHeld: 'read full source with exact party', RestoreSource: 'original source binding restored'})[action];
