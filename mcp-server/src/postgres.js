// Share PostgreSQL type decoding across HTTP and pooled connections.
import { types } from '@neondatabase/serverless';

// A DATE has no time or timezone. Preserve its PostgreSQL calendar value.
types.setTypeParser(types.builtins.DATE, value => value);

export { neon, Pool, neonConfig, types } from '@neondatabase/serverless';
