// Manage actor-owned browser subscriptions without changing notification preferences.
import type { Actor, Db, VerbInputs, WithEnvelope } from './verb-contract.ts';
import type { ToolError as ErrorType } from './tool-error.ts';

type Config = { WEB_PUSH_VAPID_PUBLIC_KEY?: string; WEB_PUSH_KEY_GENERATION?: string };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const uuidSchema = { type: 'string', pattern: UUID.source };
const common = { idempotency_key: uuidSchema, installation_id: uuidSchema };
function validKey(value: unknown, bytes: number) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/.test(value)) return false;
  try {
    const decoded = atob(value.replace(/-/g, '+').replace(/_/g, '/'));
    return decoded.length === bytes && (bytes !== 65 || decoded.charCodeAt(0) === 4)
      && btoa(decoded).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') === value;
  } catch { return false; }
}
export function webPushTools({ withEnvelope, ToolError, config }: {
  withEnvelope: WithEnvelope; ToolError: typeof ErrorType; config: () => Config;
}) {
  const fail = (error: string): never => { throw new ToolError({ error }); };
  const publicConfig = () => {
    const c = config();
    if (!validKey(c.WEB_PUSH_VAPID_PUBLIC_KEY, 65) || !c.WEB_PUSH_KEY_GENERATION
      || !/^[A-Za-z0-9._-]{1,80}$/.test(c.WEB_PUSH_KEY_GENERATION)) fail('feature_unavailable');
    return { ok: true, public_key: c.WEB_PUSH_VAPID_PUBLIC_KEY, key_generation: c.WEB_PUSH_KEY_GENERATION };
  };
  async function mutate(c: Db, a: Actor, verb: 'register-web-push-subscription' | 'revoke-web-push-subscription',
    args: VerbInputs['register-web-push-subscription'] | VerbInputs['revoke-web-push-subscription']) {
    const registering = verb === 'register-web-push-subscription';
    const allowed = registering ? [...Object.keys(common), 'endpoint', 'p256dh', 'auth', 'expirationTime'] : Object.keys(common);
    if (Object.keys(args).some(k => !allowed.includes(k)) || !UUID.test(args.idempotency_key) || !UUID.test(args.installation_id)) fail('web_push_invalid_input');
    let request: object;
    if (registering && 'endpoint' in args) {
      try {
        const endpoint = new URL(args.endpoint);
        if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.hash || args.endpoint.length > 2048
          || !validKey(args.p256dh, 65) || !validKey(args.auth, 16)
          || (args.expirationTime != null && (!Number.isSafeInteger(args.expirationTime)))) fail('web_push_invalid_input');
      } catch { fail('web_push_invalid_input'); }
      request = { installation_id: args.installation_id, endpoint: args.endpoint, p256dh: args.p256dh, auth: args.auth,
        expirationTime: args.expirationTime ?? null, key_generation: publicConfig().key_generation };
    } else if (registering) return fail('web_push_invalid_input');
    else request = { installation_id: args.installation_id };
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(request))))]
      .map(n => n.toString(16).padStart(2, '0')).join('');
    return withEnvelope(c, a, verb, { idempotency_key: args.idempotency_key, request_digest: digest }, async () => {
      let result;
      try {
        result = (await c.query('select ops.web_push_lifecycle($1::jsonb,$2::uuid,$3::text) as result',
          [request, args.idempotency_key, registering ? 'register' : 'revoke'])).rows[0]?.result;
      } catch { return fail('web_push_unavailable'); }
      if (!result?.ok) return fail(['web_push_limit', 'web_push_rate_limit', 'key_reuse', 'web_push_invalid_input'].includes(result?.error) ? result.error : 'web_push_unavailable');
      return { ok: true, subscription_id: result.subscription_id ?? null, state: result.state, generation: result.generation ?? 0 };
    });
  }
  return {
    'read-web-push-config': {
      description: 'Read the configured public browser push key and generation. Refuses when unavailable; never generates keys.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {}, required: [] },
      handler: async () => publicConfig(),
    },
    'register-web-push-subscription': {
      write: true,
      description: 'Register or refresh only the authenticated actor’s browser installation. Does not enable notification opt-in or deliver pushes. Returns safe metadata only.',
      inputSchema: { type: 'object', additionalProperties: false, properties: { ...common,
        endpoint: { type: 'string', maxLength: 2048 }, p256dh: { type: 'string', maxLength: 87 }, auth: { type: 'string', maxLength: 22 },
        expirationTime: { type: ['integer', 'null'], minimum: 1 } }, required: [...Object.keys(common), 'endpoint', 'p256dh', 'auth'] },
      handler: (c: Db, a: Actor, args: VerbInputs['register-web-push-subscription']) => mutate(c, a, 'register-web-push-subscription', args),
    },
    'revoke-web-push-subscription': {
      write: true,
      description: 'Idempotently revoke only the authenticated actor’s installation, including an expired subscription. Does not change actor-wide preferences.',
      inputSchema: { type: 'object', additionalProperties: false, properties: common, required: Object.keys(common) },
      handler: (c: Db, a: Actor, args: VerbInputs['revoke-web-push-subscription']) => mutate(c, a, 'revoke-web-push-subscription', args),
    },
  };
}
