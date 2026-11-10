const actions: Record<string, string> = {
  Change: "source changes",
  Consume: "consume source",
  Acknowledge: "acknowledge",
  Unsubscribe: "unsubscribe",
  Scan: "continue bounded batch",
  Done: "idle",
};
export const state = (v: { subscribed: boolean; delivered: readonly unknown[] }) =>
  !v.subscribed ? "Unsubscribed" : v.delivered.length ? "Notified" : "Quiet";
export const step = (a: string) => actions[a] || a;
export const title = "Watched record notifications";
