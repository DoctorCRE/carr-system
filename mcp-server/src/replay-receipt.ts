// What the tool_call replay ledger keeps for one write, and for how long.
//
// A retry within REPLAY_WINDOW replays the stored response; after it the key is
// unknown and the write runs as new. The database enforces the same window:
// migration 1048's tool_call_expire trigger frees an expired key for reuse and
// deletes a bounded batch of expired rows on every insert. Durable receipts are
// read back by key elsewhere; the trigger exempts the same verbs and rw02- keys.

export const REPLAY_WINDOW = "30 days";
export const REPLAY_RESPONSE_LIMIT = 16384;

// Keys whose receipt must replay forever, by their own shape. ops.rw02_record_run
// finds a run's page stops by the `rw02-read:<run_ref>:` prefix, and
// ops.rw02_loop_episodes finds a finding's loop episodes by the exact
// base/`:eN` shape. An absent-from-Salesforce finding is keyed per deal and
// reason, and a repeat run counts on replaying its first row instead of filing
// another. Only these shapes, on these verbs, are accepted besides a UUID.
export const DURABLE_KEY_SHAPES: Record<string, RegExp> = {
  "record-salesforce-page-stop": /^rw02-read:[0-9a-f]{24}:page-stop:[0-9]{1,6}$/,
  "add-loop": /^rw02-(?:missing-joe|unknown-to-carr):[0-9a-f]{32}(?::e(?:[2-9]|[1-9][0-9]{1,5}))?$/,
  "record-finding": /^rw02-absent:[0-9a-f]{32}$/,
};

// Work Request cards join these receipts by key to show who acted
// (work-request-intake.js ACTING_IDENTITY).
const ACTING_IDENTITY_VERBS = new Set(["review-and-triage", "accept-ready-plan", "accept-outcome-feedback"]);

export function durableReceipt(verb: string, key: string): boolean {
  return ACTING_IDENTITY_VERBS.has(verb) || DURABLE_KEY_SHAPES[verb]?.test(key) === true;
}

const small = (value: unknown) => value === null || typeof value === "boolean" || typeof value === "number"
  || (typeof value === "string" && value.length <= 200);

// The stored response as JSON text. One over the limit is replaced by its small
// top-level fields (ids, versions, ok) and a note: the write already ran, so a
// replay tells the caller to read the record rather than echoing a large body.
export function replayReceipt(result: Record<string, unknown>): string {
  const body = JSON.stringify(result);
  if (body.length <= REPLAY_RESPONSE_LIMIT) return body;
  return JSON.stringify({
    ...Object.fromEntries(Object.entries(result).filter(([, value]) => small(value))),
    response_omitted: { chars: body.length, reread: "the write already ran; read the record for its current state" },
  });
}
