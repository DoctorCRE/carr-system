// Parse the deployment-bound Workspace Command Center read flag.
type WorkspaceEnv = Partial<Pick<Env, typeof WORKSPACE_COMMAND_CENTER_FLAG>>;

export const WORKSPACE_COMMAND_CENTER_FLAG = "WORKSPACE_COMMAND_CENTER_READ_ENABLED";

export function workspaceCommandCenterPosture(env?: WorkspaceEnv | null) {
  const value = env?.[WORKSPACE_COMMAND_CENTER_FLAG];
  if (value === "true") return { enabled: true, posture: "enabled", reason: null };
  if (value === "false") return { enabled: false, posture: "disabled", reason: null };
  return { enabled: false, posture: "misconfigured",
    reason: "WORKSPACE_COMMAND_CENTER_READ_ENABLED must be exactly true or false" };
}

export function workspaceCommandCenterEnabled(env?: WorkspaceEnv | null) {
  return workspaceCommandCenterPosture(env).enabled;
}
