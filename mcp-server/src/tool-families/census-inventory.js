// Register this verb family without editing the shared tool list.
import { censusInventoryTools } from "../census-inventory.js";

export default {
  source: "mcp-server/src/census-inventory.js",
  createTools: ({ withEnvelope }) => censusInventoryTools({ withEnvelope }),
};
