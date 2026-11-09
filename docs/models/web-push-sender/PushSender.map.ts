export const regions = ["delivery", "attemptAuthority", "subscription", "submission"];
export const readerActions = ["Revoke"];
const labels: Record<string, string> = {
  Pending: "Waiting to notify",
  Held: "Notification held",
  Submitting: "Notification being submitted",
  Accepted: "Accepted by push service",
  Failed: "Notification failed",
  Unknown: "Submission outcome unknown",
  Cancelled: "Notification cancelled",
  Expired: "Notification expired",
  RetryAllowed: "Retry is permitted",
  Ambiguous: "Automatic retry stopped",
  Active: "Installation active",
  Revoked: "Installation revoked",
  Idle: "No HTTP request",
  Sending: "Submitting to push service",
};
export function state(value: Record<string, string>) {
  return regions.map((key) => labels[value[key] ?? ""] ?? value[key]).join(" | ");
}
export function step(action: string) {
  return action.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
}
