export const state = (v: { alias: string; phase: string }) => `${v.alias}: ${v.phase}`;
export const title = "Email signature findings across decoder aliases";
export const step = (action: string) => actions[action];
const actions: Record<string, string> = {
  Resolve: "resolve sender and inspect signature",
  Submit: "submit frozen proposal",
  Retry: "retry or hold after validation",
  AliasChange: "prove decoder alias by unambiguous Message-ID",
  AmbiguousDuplicate: "keep duplicate store keys separate",
  Restart: "reload frozen journal",
  Replay: "repeat scan",
  Done: "keep disposition",
  RecoverHeld: "read full source with exact party",
  RestoreSource: "original source binding restored",
};
