// Register this verb family without editing the shared tool list.
import { practiceOriginationTools } from "../practice-origination.ts";

export default {
  source: "mcp-server/src/practice-origination.ts",
  createTools: ({ withEnvelope }) => practiceOriginationTools({ withEnvelope }),
};
