// Expose client portal tools.
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs, WithEnvelope, WriteEvent } from './verb-contract.ts';
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
type Deps = { withEnvelope: WithEnvelope; writeEvent: WriteEvent;
  resolveSubject: (c: Db, ref: string) => Promise<{ type: string; id: string }> };
// ops.register_client_portal raises these; each is a refusal the partner can act on.
const REGISTER_REFUSALS = ['portal_slug_invalid', 'portal_password_invalid', 'portal_deal_not_found',
  'portal_slug_taken', 'portal_deal_already_registered'];
export function clientPortalTools({ withEnvelope, writeEvent, resolveSubject }: Deps) {
  return {
    'register-client-portal': {
      write: true,
      humanOnly: true,
      description: 'Register the password-protected client portal for one deal at share.doctorcre.com/<slug>. Stores only the bcrypt verifier, never the password, and never returns either. A slug already bound to another client or deal is refused with portal_slug_taken; a deal with an active portal is refused with portal_deal_already_registered. Repeating the same slug and password for the same deal succeeds without a second row. The password is the partner\'s simple choice; no complexity rules apply.',
      inputSchema: {
        type: 'object', additionalProperties: false,
        properties: { idempotency_key: { type: 'string' }, deal: { type: 'string', description: 'deal name or id' },
          slug: { type: 'string', pattern: '^[a-z0-9][a-z0-9-]{0,62}$' },
          password: { type: 'string', minLength: 1, maxLength: 1024 } },
        required: ['idempotency_key', 'deal', 'slug', 'password'],
      },
      // The password stays out of the envelope: tool_call keeps a hash of the arguments, and a
      // fast hash of a simple password is not worth keeping.
      handler: async (c: Db, actor: Actor, args: VerbInputs['register-client-portal']) => {
        const { password, ...kept } = args;
        return withEnvelope(c, actor, 'register-client-portal', kept, async () => {
          const subject = await resolveSubject(c, args.deal);
          if (subject.type !== 'deal') throw new ToolError({ error: 'not_a_deal', resolved: subject });
          let outcome: string;
          try {
            outcome = (await c.query('select ops.register_client_portal($1::uuid,$2::text,$3::text) as outcome',
              [subject.id, args.slug, password])).rows[0].outcome;
          } catch (error) {
            const named = REGISTER_REFUSALS.find(code => (error as Error).message === code);
            if (named) throw new ToolError({ error: named });
            throw error;
          }
          const portal = (await c.query('select client_portal_url from v_deal_client_portal where deal_id=$1',
            [subject.id])).rows[0];
          if (outcome === 'created')
            await writeEvent(c, actor, 'register-client-portal', 'deal', subject.id,
              { field: 'client_portal', new: { client_portal_url: portal.client_portal_url },
                idempotency_key: args.idempotency_key });
          return { ok: true, deal_id: subject.id, client_portal_url: portal.client_portal_url,
            created: outcome === 'created' };
        });
      },
    },
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
