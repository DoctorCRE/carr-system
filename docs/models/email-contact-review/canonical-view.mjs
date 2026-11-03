export function state(v) { return v.badMerge || v.badBinding ? "Source held incorrectly" : v.phase === "submit" ? "Frozen proposal ready" : "Waiting for mail"; }
export function step(a) { return ({Scan:"Read mailbox",Resolve:"Identify source",Submit:"Record proposal",Restart:"Restart intake"})[a] || a; }
export const readerActions = ["Scan", "Restart"];
