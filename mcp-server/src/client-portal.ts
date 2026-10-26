import { ToolError } from './tool-error.ts';
import type { Db, VerbInputs } from './verb-contract.ts';
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
export function clientPortalTools() {
  return {
    'read-client-portal': {
      write: true,
      description: 'Read one password-protected client-portal.v1 roadmap and explicitly shared text material. Requires authenticated CARR access. This is not a public login endpoint. The server locks a slug after five failed passwords within 15 minutes. Never accepts client or deal selectors.',
      inputSchema: {
        type: 'object', additionalProperties: false,
        properties: { slug: { type: 'string', pattern: '^[a-z0-9][a-z0-9-]{0,62}$' }, password: { type: 'string', minLength: 1, maxLength: 1024 } },
        required: ['slug', 'password'],
      },
      handler: async (c: Db, _actor: unknown, args: VerbInputs['read-client-portal']) => {
        if (Object.keys(args).some(key => !['slug', 'password'].includes(key)) ||
            typeof args.slug !== 'string' || !SLUG.test(args.slug) ||
            typeof args.password !== 'string' || !args.password.length || args.password.length > 1024)
          throw new ToolError({ error: 'invalid_portal_request' });
        const rows = await c.query('select ops.read_client_portal($1::text,$2::text) as portal', [args.slug, args.password]);
        const portal = rows.rows[0]?.portal;
        if (!portal) return { ok: false, error: 'portal_access_denied' };
        return { ok: true, schema_version: 'client-portal.v1', portal };
      },
    },
  };
}
