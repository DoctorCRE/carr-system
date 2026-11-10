// Share PostgreSQL type decoding across HTTP and pooled connections.
import { types } from "@neondatabase/serverless";

// A DATE has no time or timezone. Preserve its PostgreSQL calendar value.
const dateOid = types.builtins.DATE;
if (dateOid === undefined) throw new Error("PostgreSQL DATE type is unavailable");
types.setTypeParser(dateOid, (value) => value);

export { neon, neonConfig, Pool, types } from "@neondatabase/serverless";
