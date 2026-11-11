// Register this verb family without editing the shared tool list.
import { vendorMeetingTools } from "../vendor-meetings.js";

import { vendorMarketTools } from "../vendor-market-registry.ts";

import { organizationTenantForActor } from "../identity.js";
import { canExercisePartnerAuthority } from "../partner-authority.js";

export default {
  source: "mcp-server/src/vendor-meetings.js",
  createTools: ({  }) => ({...vendorMeetingTools(), ...vendorMarketTools({canExercisePartnerAuthority,organizationTenantForActor})}),
};
