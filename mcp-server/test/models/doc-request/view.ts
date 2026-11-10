const phases: Record<string, string> = { absent: "No new ask", pending: "Ask saving", accepted: "Ask accepted" };
const answers: Record<string, string> = {
  idle: "answer not yet saved",
  pending: "answer saving",
  committed: "answer saved",
};
const actions: Record<string, string> = {
  Submit: "Partner submits",
  Commit: "Ask and receipt commit",
  Rollback: "Receipt write fails",
  Answer: "Assistant saves answer",
  AnswerCommit: "Answer commits",
  AnswerRollback: "Answer transaction fails",
  Wait: "Other writer waits",
  Revoke: "Partner access revoked",
  Replay: "Identical retry",
  Duplicate: "Concurrent duplicate retries",
  Refuse: "Request refused",
};
export const title = "Queued ask and assistant answer";
export const state = (v: { phase: string; authorized: boolean; answer: string }) =>
  `${phases[v.phase]}, ${v.authorized ? "access active" : "access revoked"}, ${answers[v.answer]}`;
export const step = (action: string) => actions[action];
