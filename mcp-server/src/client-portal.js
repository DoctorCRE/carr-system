const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
const MATERIAL_KINDS = new Set(['document', 'tour', 'report']);

function projectPortal(value, slug) {
  if (!value || value.slug !== slug || !value.roadmap ||
      typeof value.roadmap.current_phase !== 'string' || !Array.isArray(value.roadmap.phases) ||
      !Array.isArray(value.materials) || value.materials.length > 100) return null;
  const phases = [];
  for (const phase of value.roadmap.phases) {
    if (!phase || typeof phase.slug !== 'string' || typeof phase.label !== 'string') return null;
    phases.push({ slug: phase.slug, label: phase.label });
  }
  if (!phases.some(phase => phase.slug === value.roadmap.current_phase)) return null;
  const materials = [];
  for (const item of value.materials) {
    if (!item || !MATERIAL_KINDS.has(item.kind) || typeof item.title !== 'string' ||
        !item.title.trim() || item.title.length > 200 || typeof item.content !== 'string' ||
        !item.content.trim() || item.content.length > 10000) return null;
    materials.push({ kind: item.kind, title: item.title, content: item.content });
  }
  return { slug, roadmap: { current_phase: value.roadmap.current_phase, phases }, materials };
}

export function clientPortalTools({ ToolError }) {
  return {
    'read-client-portal': {
      description: 'Read one password-protected client-portal.v1 roadmap and explicitly shared text material. Requires authenticated CARR access. This is not a public login endpoint; a future public consumer must throttle password attempts. Never accepts client or deal selectors.',
      inputSchema: {
        type: 'object', additionalProperties: false,
        properties: { slug: { type: 'string', pattern: '^[a-z0-9][a-z0-9-]{0,62}$' }, password: { type: 'string', minLength: 1, maxLength: 1024 } },
        required: ['slug', 'password'],
      },
      handler: async (c, _actor, args) => {
        if (Object.keys(args).some(key => !['slug', 'password'].includes(key)) ||
            typeof args.slug !== 'string' || !SLUG.test(args.slug) ||
            typeof args.password !== 'string' || !args.password.length || args.password.length > 1024)
          throw new ToolError({ error: 'invalid_portal_request' });
        const rows = await c.query('select ops.read_client_portal($1::text,$2::text) as portal', [args.slug, args.password]);
        const portal = projectPortal(rows.rows[0]?.portal, args.slug);
        if (!portal) throw new ToolError({ error: 'portal_access_denied' });
        return { ok: true, schema_version: 'client-portal.v1', portal };
      },
    },
  };
}
