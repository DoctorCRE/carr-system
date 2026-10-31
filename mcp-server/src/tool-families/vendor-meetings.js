// Register this verb family without editing the shared tool list.
import { vendorMeetingTools } from "../vendor-meetings.js";

export default {
  source: "mcp-server/src/vendor-meetings.js",
  createTools: ({  }) => vendorMeetingTools(),
};
