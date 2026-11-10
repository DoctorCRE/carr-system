const actions: Record<string, string | null> = {
  Start: "work starts",
  ResolveWork: "canonical work resolves",
  ResolveFactReview: "fact review resolves",
  ReceiveTourFeedback: "client feedback received",
  Reopen: "canonical work reopens",
  Snooze: "snooze",
  Expire: "snooze expires",
  Settle: "settle",
  Unsettle: "undo settlement or snooze",
  Stay: null,
};
export const title = "Home inbox settlement";
export const state = (v: { disposition: string; override: boolean; source: string }) =>
  v.disposition !== "inbox"
    ? v.disposition === "snoozed"
      ? "Snoozed"
      : "Settled manually"
    : v.override
      ? "Explicitly returned to Inbox"
      : v.source === "done"
        ? "Auto-settled"
        : v.source === "working"
          ? "Working"
          : "Open";
export const step = (action: string) => actions[action];
