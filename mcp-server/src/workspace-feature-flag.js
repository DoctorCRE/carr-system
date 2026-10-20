// @ts-check

/** @typedef {Partial<Pick<Env, typeof WORKSPACE_COMMAND_CENTER_FLAG>>} WorkspaceEnv */

export const WORKSPACE_COMMAND_CENTER_FLAG = "WORKSPACE_COMMAND_CENTER_READ_ENABLED";

/** @param {WorkspaceEnv | null} [env] */
export function workspaceCommandCenterPosture(env) {
  const value = env?.[WORKSPACE_COMMAND_CENTER_FLAG];
  if (value === "true") return { enabled: true, posture: "enabled", reason: null };
  if (value === "false") return { enabled: false, posture: "disabled", reason: null };
  return { enabled: false, posture: "misconfigured",
    reason: "WORKSPACE_COMMAND_CENTER_READ_ENABLED must be exactly true or false" };
}

/** @param {WorkspaceEnv | null} [env] */
export function workspaceCommandCenterEnabled(env) {
  return workspaceCommandCenterPosture(env).enabled;
}
