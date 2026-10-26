export const CARR_STAGING_HOST = "carr-mcp-staging.joe-bookout-carr-us.workers.dev";
export const APP_STAGING_HOST = "doctorcre-app-staging.joe-bookout-carr-us.workers.dev";

type StagingHostEnv = {
  CARR_ENV?: string; PRIMARY_APP_HOST?: string; APP_HOST?: string;
  DEALROOM_HOST?: string; DOCTORCRE_APP_HOST?: string;
};

export function isStagingBrowserEnvironment(env: StagingHostEnv | null | undefined): boolean {
  const primaryHost = env?.PRIMARY_APP_HOST || env?.APP_HOST || env?.DEALROOM_HOST;
  const host = env?.APP_HOST || env?.DEALROOM_HOST;
  return env?.CARR_ENV === "staging" && primaryHost === CARR_STAGING_HOST &&
    ([CARR_STAGING_HOST, APP_STAGING_HOST] as (string | undefined)[]).includes(host) &&
    (!env.DOCTORCRE_APP_HOST || env.DOCTORCRE_APP_HOST === APP_STAGING_HOST);
}
