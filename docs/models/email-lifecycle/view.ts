export const title = "Captured mail lifecycle";
export const state = (v: { state: string }) => v.state;
export const step = (action: string) => actions[action];
const actions: Record<string, string | null> = {
  Act: "act on live mail",
  Observe: "observe terminal state and note earlier actions",
  Replay: "replay observation",
  RefuseStaleOrForeign: null,
};
