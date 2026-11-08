const watches={Watching:'Watch on',Paused:'Watch paused',Removed:'Watch removed'};
const receipts={Quiet:'no waiting change',Pending:'change waiting',Emitted:'alert delivered'};
const inboxes={Quiet:'no inbox card',Visible:'card visible',Settled:'card settled'};
const health={Healthy:'processing healthy',Delayed:'processing delayed',NeedsRepair:'processing needs repair'};
export const state = v => `${watches[v.watch]}, ${receipts[v.receipt]}, ${inboxes[v.inbox]}, ${health[v.health]}`;
export const step = a => ({Capture:'A meaningful change arrives',Emit:'The alert appears',Pause:'Pause watch',Resume:'Resume quietly',Unwatch:'Remove watch',Failure:'Processing fails',Repair:'Repair processing',AccessLost:'Access is withdrawn',Settle:'Settle the card',Replay:'Retry the same change',ReadFailure:'The read is unavailable',ReadSuccess:'The confirmed watch is read'}[a]??a);
export const readerActions=[];
