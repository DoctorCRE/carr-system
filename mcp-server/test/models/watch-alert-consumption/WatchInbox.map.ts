export const regions = ["notificationVisibility", "homePlacement", "canonicalWatchSubject"];
const labels: Record<string, string> = {
  Acknowledge: "Read the alert",
  Settle: "Settle the card",
  Unsettle: "Return the card to Inbox",
  Snooze: "Snooze the card",
};
export const readerActions = Object.keys(labels);
export const state = (v: { notificationVisibility: string; homePlacement: string }) =>
  `${v.notificationVisibility} alert | ${v.homePlacement} card | Work still open`;
export const step = (action: string) => labels[action] ?? action;
