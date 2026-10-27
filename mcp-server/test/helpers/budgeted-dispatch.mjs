import { dispatch as dispatchRequest } from "../../src/mcp.js";

export const ALLOW_VERB_BUDGET = {
  idFromName: name => name,
  get: () => ({ fetch: async () => Response.json({ allowed: true }) }),
};

export function dispatch(request, env, ctx, actor) {
  env.VERB_REQUEST_BUDGET = ALLOW_VERB_BUDGET;
  return dispatchRequest(request, env, ctx, actor);
}
