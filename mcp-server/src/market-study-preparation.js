import { webcrypto } from 'node:crypto';
import { composeMarketStudyPackage } from './market-study-composer.js';
import { ToolError } from './tool-error.ts';
import contract from '../../workspace/contracts/market-study-publication.v1.json' with { type: 'json' };

const LIMITS = Object.freeze(contract.limits);
const VERSIONS = Object.freeze(contract.versions);
const REQUIRED_REVISIONS = contract.interface.snapshot.revisions_required;
const TTL = LIMITS.token_ttl_ms;
const fail = (error, extra = {}) => { throw new ToolError({ error, ...extra }); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 200;
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

function canonical(value) {
  let nodes = 0, bytes = 0;
  function visit(item, depth) {
    if (++nodes > LIMITS.nodes || depth > LIMITS.depth) fail('payload_limit');
    if (item === null || typeof item === 'boolean') return JSON.stringify(item);
    if (typeof item === 'number' && Number.isFinite(item)) return JSON.stringify(item);
    if (typeof item === 'string') {
      bytes += Buffer.byteLength(item);
      if (bytes > LIMITS.bytes) fail('payload_limit');
      return JSON.stringify(item);
    }
    if (Array.isArray(item)) return '[' + item.map(child => visit(child, depth + 1)).join(',') + ']';
    if (!object(item)) fail('invalid_json');
    return '{' + Object.keys(item).sort().map(key => visit(key, depth + 1) + ':' + visit(item[key], depth + 1)).join(',') + '}';
  }
  const encoded = visit(value, 0);
  if (Buffer.byteLength(encoded) > LIMITS.bytes) fail('payload_limit');
  return encoded;
}

async function digest(encoded) {
  return Buffer.from(await webcrypto.subtle.digest('SHA-256', Buffer.from(encoded))).toString('hex');
}
export async function packageDigest(value) { return digest(canonical(value)); }

function selector(args, preview) {
  const allowed = preview ? ['deal_id', 'research_package_id', 'research_version', 'renderer_version'] : ['deal_id', 'limit', 'after'];
  if (!object(args) || Object.keys(args).some(key => !allowed.includes(key)) || !text(args.deal_id)) fail('invalid_selector');
  if (preview && (!text(args.research_package_id) || !Number.isSafeInteger(args.research_version) || args.research_version < 1)) fail('invalid_selector');
  if (preview && args.renderer_version !== VERSIONS.renderer) fail('unsupported_renderer');
  if (!preview && ((args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > LIMITS.page)) || (args.after !== undefined && (!object(args.after) || Object.keys(args.after).sort().join(',') !== 'id,version' || !text(args.after.id) || !Number.isSafeInteger(args.after.version) || args.after.version < 1)))) fail('invalid_selector');
}

function validateSnapshot(snapshot, args) {
  if (!object(snapshot) || snapshot.deal_id !== args.deal_id || !text(snapshot.client_id)) fail('snapshot_unavailable');
  canonical(snapshot);
  const research = snapshot.research;
  if (!object(research) || research.id !== args.research_package_id || research.version !== args.research_version ||
    research.deal_id !== snapshot.deal_id || research.client_id !== snapshot.client_id) fail('research_identity_mismatch');
  if (!hash(research.sha256)) fail('research_digest_mismatch');
  const revisions = snapshot.revisions;
  if (!object(revisions) || REQUIRED_REVISIONS.some(key => !Object.hasOwn(revisions, key)) ||
    Object.keys(revisions).length > LIMITS.revisions || Object.keys(revisions).some(key => !text(key)) || Object.values(revisions).some(value =>
      !(Number.isSafeInteger(value) && value >= 0) && !hash(value))) fail('revision_map_invalid');
  if (!Array.isArray(snapshot.assets) || snapshot.assets.length > LIMITS.assets ||
    !Array.isArray(snapshot.reviewed?.properties) || snapshot.reviewed.properties.length > LIMITS.properties) fail('snapshot_limit');
}

export function createMarketStudyPreparation({ authorize, readSnapshot, listPackages, signingKey, clock = Date.now }) {
  async function context(actor, args) {
    if (!text(actor?.id)) fail('unauthorized_actor');
    const scope = await authorize(actor, args.deal_id);
    if (!text(scope)) fail('unauthorized_actor');
    return scope;
  }
  function key() {
    if (!signingKey || signingKey.type !== 'secret' || signingKey.algorithm?.name !== 'HMAC' || signingKey.algorithm?.hash?.name !== 'SHA-256' || signingKey.algorithm?.length < 256 || !signingKey.usages.includes('sign') || !signingKey.usages.includes('verify')) fail('signing_unavailable');
    return signingKey;
  }
  async function candidate(actor, args) {
    const scope = await context(actor, args);
    const snapshot = await readSnapshot(actor, args);
    validateSnapshot(snapshot, args);
    if (await packageDigest(snapshot.research.data) !== snapshot.research.sha256) fail('research_digest_mismatch');
    const target = { deal_id: snapshot.deal_id, client_id: snapshot.client_id };
    const composed = composeMarketStudyPackage(snapshot.research.data, snapshot.reviewed, target,
      { research: { deal_id: snapshot.research.deal_id, client_id: snapshot.research.client_id }, assets: snapshot.assets });
    if (!composed.ok) fail('composition_invalid', { stage: composed.stage, errors: composed.errors });
    const binding = {
      ...VERSIONS, actor_id: actor.id, authorization_scope: scope, ...target,
      research_package_id: snapshot.research.id, research_version: snapshot.research.version,
      research_sha256: snapshot.research.sha256, revisions: snapshot.revisions,
      overlay_sha256: await packageDigest(snapshot.reviewed), asset_membership_sha256: await packageDigest(snapshot.assets),
      package_sha256: await packageDigest(composed.package),
    };
    return { package: composed.package, binding };
  }
  return {
    async list(actor, args) {
      selector(args, false);
      await context(actor, args);
      const limit = args.limit ?? LIMITS.page;
      const rows = await listPackages(actor, { deal_id: args.deal_id, limit: limit + 1, ...(args.after ? { after: args.after } : {}) });
      if (!Array.isArray(rows) || rows.length > limit + 1) fail('catalog_limit');
      const packages = rows.slice(0, limit).map(row => {
        if (!text(row.id) || !Number.isSafeInteger(row.version) || row.version < 1 || !hash(row.sha256) || !text(row.title)) fail('catalog_invalid');
        return { id: row.id, version: row.version, sha256: row.sha256, title: row.title, schema: VERSIONS.package_schema };
      });
      canonical(packages);
      return { ok: true, schema_version: VERSIONS.schema, packages,
        next: rows.length > limit ? { id: packages.at(-1).id, version: packages.at(-1).version } : null };
    },
    async prepare(actor, args) {
      selector(args, true);
      const secret = key();
      const result = await candidate(actor, args);
      const issued = clock();
      if (!Number.isSafeInteger(issued) || issued < 0 || !Number.isSafeInteger(issued + TTL)) fail('clock_unavailable');
      const binding = { ...result.binding, issued_at: issued, expires_at: issued + TTL };
      const payload = Buffer.from(canonical(binding)).toString('base64url');
      const signature = Buffer.from(await webcrypto.subtle.sign('HMAC', secret, Buffer.from(payload))).toString('base64url');
      const token = payload + '.' + signature;
      if (token.length > LIMITS.token_characters) fail('preview_limit');
      return { ok: true, schema_version: VERSIONS.schema, package: result.package, binding, preview_token: token };
    },
    async verify(actor, args, token) {
      selector(args, true);
      const secret = key();
      if (typeof token !== 'string' || token.length > LIMITS.token_characters || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/.test(token)) fail('preview_invalid');
      const [payload, signature] = token.split('.');
      if (Buffer.from(signature, 'base64url').toString('base64url') !== signature) fail('preview_invalid');
      if (!await webcrypto.subtle.verify('HMAC', secret, Buffer.from(signature, 'base64url'), Buffer.from(payload))) fail('preview_invalid');
      let binding;
      try { binding = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8')); }
      catch { fail('preview_invalid'); }
      const now = clock();
      if (!object(binding) || !Number.isSafeInteger(now) || !Number.isSafeInteger(binding.issued_at) ||
        !Number.isSafeInteger(binding.expires_at) || binding.expires_at - binding.issued_at !== TTL || binding.issued_at > now) fail('preview_invalid');
      if (binding.expires_at <= now) fail('preview_expired');
      const result = await candidate(actor, args);
      const { issued_at, expires_at, ...expected } = binding;
      if (canonical(expected) !== canonical(result.binding)) fail('preview_stale');
      return { ok: true, package: result.package, binding };
    },
  };
}
