export const state = (v: unknown) => {
  if (
    typeof v !== "object" ||
    v === null ||
    !("cursor" in v) ||
    typeof v.cursor !== "number" ||
    !("emitted" in v) ||
    !Array.isArray(v.emitted) ||
    !v.emitted.every((item: unknown) => typeof item === "number") ||
    !("unlocked" in v) ||
    typeof v.unlocked !== "boolean"
  )
    throw new Error("invalid_queue_model_state");
  return `Queue ${v.cursor === 2 ? "at end" : "advancing"}, alerts ${v.emitted.length ? v.emitted.join(", ") : "none"}, head ${v.unlocked ? "unlocked" : "locked"}`;
};
const actions: Record<string, string> = {
  Attempt: "Try the next waiting watch",
  Wrap: "Start the next queue pass",
  Unlock: "The held transaction finishes",
};
export const step = (a: string) => actions[a] ?? a;
export const readerActions: string[] = [];
