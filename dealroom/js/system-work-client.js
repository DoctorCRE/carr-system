import { uuidv4 } from "./uuid.js";
import { createSystemWorkTransport } from "./web-transport.js";
import { validateHumanRef } from "./system-work-view.js";

export function createSystemWorkClient(options = {}) {
  const uuid = options.uuid || uuidv4;
  const transport = createSystemWorkTransport({ fetchImpl: options.fetchImpl });
  const { bootstrap, get, post } = transport;

  async function challenge(action, material) {
    return post("/api/system-work/challenge", { action, ...material });
  }

  const withKey = (body) => ({ ...body, idempotency_key: body.idempotency_key || uuid() });

  return {
    get session() { return transport.session; },
    bootstrap,
    current: () => get("/api/system-work/current"),
    async read(humanRef) {
      return get(`/api/system-work/${encodeURIComponent(validateHumanRef(humanRef))}`);
    },
    report: (body) => post("/api/system-work/report", withKey(body)),
    triage: (humanRef, body) => post(`/api/system-work/${validateHumanRef(humanRef)}/triage`, withKey(body)),
    preparePlan: (humanRef, body) => post(`/api/system-work/${validateHumanRef(humanRef)}/plan`, withKey(body)),
    async acceptPlan(humanRef, body) {
      const ref = validateHumanRef(humanRef);
      const requestBody = withKey(body);
      const material = { action: "accept-ready-plan", human_ref: ref,
        base_version: requestBody.base_version, plan_hash: requestBody.plan_hash,
        idempotency_key: requestBody.idempotency_key };
      const receipt = await challenge(material.action, { human_ref: ref,
        base_version: requestBody.base_version, plan_hash: requestBody.plan_hash,
        idempotency_key: requestBody.idempotency_key });
      return post(`/api/system-work/${ref}/plan/accept`, requestBody, receipt.challenge);
    },
    proposeOutcome: (humanRef, body) => post(`/api/system-work/${validateHumanRef(humanRef)}/outcomes`, withKey(body)),
    async acceptOutcome(humanRef, body) {
      const ref = validateHumanRef(humanRef);
      const requestBody = withKey(body);
      const receipt = await challenge("accept-outcome-feedback", { human_ref: ref,
        base_version: requestBody.base_version, feedback_hash: requestBody.feedback_hash,
        idempotency_key: requestBody.idempotency_key });
      return post(`/api/system-work/${ref}/outcomes/accept`, requestBody, receipt.challenge);
    },
  };
}
