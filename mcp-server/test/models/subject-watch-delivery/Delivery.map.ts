const watches: Record<string, string> = { Watching: "Watch on", Paused: "Watch paused", Removed: "Watch removed" };
const receipts: Record<string, string> = {
  Quiet: "no waiting change",
  Pending: "change waiting",
  Emitted: "alert delivered",
};
const inboxes: Record<string, string> = { Quiet: "no inbox card", Visible: "card visible", Settled: "card settled" };
const health: Record<string, string> = {
  Healthy: "processing healthy",
  Delayed: "processing delayed",
  NeedsRepair: "processing needs repair",
};
export const state = (v: unknown) => {
  if (
    typeof v !== "object" ||
    v === null ||
    !("watch" in v) ||
    typeof v.watch !== "string" ||
    !("receipt" in v) ||
    typeof v.receipt !== "string" ||
    !("inbox" in v) ||
    typeof v.inbox !== "string" ||
    !("health" in v) ||
    typeof v.health !== "string"
  )
    throw new Error("invalid_delivery_model_state");
  return `${watches[v.watch]}, ${receipts[v.receipt]}, ${inboxes[v.inbox]}, ${health[v.health]}`;
};
const actions: Record<string, string> = {
  Capture: "A meaningful change arrives",
  Emit: "The alert appears",
  Pause: "Pause watch",
  Resume: "Resume quietly",
  Unwatch: "Remove watch",
  Failure: "Processing fails",
  Repair: "Repair processing",
  AccessLost: "Access is withdrawn",
  Settle: "Settle the card",
  Replay: "Retry the same change",
  ReadFailure: "The read is unavailable",
  ReadSuccess: "The confirmed watch is read",
};
export const step = (a: string) => actions[a] ?? a;
export const readerActions: string[] = [];
