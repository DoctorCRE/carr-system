type Program6Env = Partial<Pick<Env, "DEALROOM_PROGRAM6_ACTIONS_ENABLED">>;

// One deployment-bound flag parser shared by the browser gate and /release.
// Only the literal string "true" enables Program 6; every other value fails
// closed.  Do not store this flag as a secret: it is reviewed configuration
// fingerprinted by the immutable release manifest.
export const PROGRAM6_ACTIONS_FLAG = "DEALROOM_PROGRAM6_ACTIONS_ENABLED";

export function program6ActionPosture(env?: Program6Env | null) {
  const value = env && env[PROGRAM6_ACTIONS_FLAG];
  if (value === "true") return { enabled: true, posture: "enabled", reason: null };
  if (value === "false") return { enabled: false, posture: "disabled", reason: null };
  return {
    enabled: false,
    posture: "misconfigured",
    reason: "DEALROOM_PROGRAM6_ACTIONS_ENABLED must be exactly true or false",
  };
}

export function program6ActionsEnabled(env?: Program6Env | null) {
  return program6ActionPosture(env).enabled;
}
