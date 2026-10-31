// Register this verb family without editing the shared tool list.
import { scheduleBoardTools } from "../schedule-board.js";

export default {
  source: "mcp-server/src/schedule-board.js",
  createTools: ({  }) => scheduleBoardTools(),
};
