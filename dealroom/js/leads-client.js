import { uuidv4 } from "./uuid.js";
import { createMcpTransport } from "./web-transport.js";

/** A deliberately small MCP client: this board reads the complete lead universe
 * and can make one bounded change, stage. Authentication remains the host's
 * same-origin cookie; there is no client-side identity or alternate endpoint. */
export function createLeadBoardClient(options = {}) {
  const uuid = options.uuid || uuidv4;
  const rpc = createMcpTransport({ fetchImpl: options.fetchImpl, surface: "lead-board" });

  return {
    getLeadBoard: () => rpc("lead-board"),
    moveLeadStage(lead, stage) {
      return rpc("update-lead", {
        lead: lead.registry_ref || lead.id,
        base_version: lead.base_version,
        fields: { stage },
        idempotency_key: uuid(),
      });
    },
  };
}
