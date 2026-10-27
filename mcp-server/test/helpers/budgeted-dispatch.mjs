import { dispatch as dispatchRequest } from "../../src/mcp.js";

const ALLOW_VERB_BUDGET = {
  idFromName: name => name,
  get: () => ({ fetch: async () => Response.json({ allowed: true }) }),
};

export function dispatch(request, env, ctx, actor) {
  env.VERB_REQUEST_BUDGET = ALLOW_VERB_BUDGET;
  env.VERB_REQUEST_BUDGET_HUMAN = env.VERB_REQUEST_BUDGET_AGENT = {
    limit: async () => ({ success: true }),
  };
  return dispatchRequest(request, env, ctx, actor);
}
