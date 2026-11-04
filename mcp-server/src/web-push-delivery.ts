import { buildPushPayload } from '@block65/webcrypto-web-push';
import type { Db } from './verb-contract.ts';

export type PushConfig = {
  WEB_PUSH_ENABLED?: string;
  WEB_PUSH_VAPID_PUBLIC_KEY?: string;
  WEB_PUSH_VAPID_PRIVATE_KEY?: string;
  WEB_PUSH_VAPID_SUBJECT?: string;
  WEB_PUSH_KEY_GENERATION?: string;
};
type ReadyConfig = { subject: string; publicKey: string; privateKey: string; generation: string };
export type Claim = {
  event_id: string; subscription_id: string; claim_token: string; card_item_id: string;
  endpoint: string; p256dh: string; auth: string; ttl: number; key_generation: string;
};
export type DeliveryStore = {
  enqueue(): Promise<unknown>;
  claim(): Promise<Claim[]>;
  authorize(claim: Claim, generation: string): Promise<boolean>;
  finish(claim: Claim, status: number, retrySeconds: number): Promise<unknown>;
};
type Sender = (claim: Claim, payload: object, config: ReadyConfig) => Promise<Response>;

export function pushConfiguration(c: PushConfig): ReadyConfig | null {
  if (c.WEB_PUSH_ENABLED !== 'true' || !c.WEB_PUSH_VAPID_PUBLIC_KEY || !c.WEB_PUSH_VAPID_PRIVATE_KEY
    || !/^[A-Za-z0-9_-]{87}$/.test(c.WEB_PUSH_VAPID_PUBLIC_KEY)
    || !/^[A-Za-z0-9_-]{43}$/.test(c.WEB_PUSH_VAPID_PRIVATE_KEY)
    || !c.WEB_PUSH_KEY_GENERATION || !/^[A-Za-z0-9._-]{1,80}$/.test(c.WEB_PUSH_KEY_GENERATION) || !c.WEB_PUSH_VAPID_SUBJECT
    || !/^(mailto:[^\s@]+@[^\s@]+|https:\/\/[^\s]+)$/.test(c.WEB_PUSH_VAPID_SUBJECT)) return null;
  return { subject:c.WEB_PUSH_VAPID_SUBJECT,publicKey:c.WEB_PUSH_VAPID_PUBLIC_KEY,
    privateKey:c.WEB_PUSH_VAPID_PRIVATE_KEY,generation:c.WEB_PUSH_KEY_GENERATION };
}

export function providerOrigin(endpoint: string): string | null {
  try {
    const u = new URL(endpoint);
    const known = u.hostname === 'fcm.googleapis.com' || u.hostname === 'updates.push.services.mozilla.com'
      || u.hostname === 'web.push.apple.com' || /^[a-z0-9-]+\.notify\.windows\.com$/.test(u.hostname);
    if (!known || endpoint !== u.href || u.protocol !== 'https:' || u.port || u.username || u.password || u.hash
      || endpoint.length > 2048 || /[\s\\]/.test(endpoint)) return null;
    return u.origin;
  } catch { return null; }
}

export function retryAfterSeconds(value: string | null, now = Date.now()): number {
  if (!value) return 0;
  const seconds = /^\d+$/.test(value) ? Number(value) : (Date.parse(value) - now) / 1000;
  return Number.isFinite(seconds) ? Math.max(0, Math.min(86400, Math.ceil(seconds))) : 0;
}

export const sendWebPush: Sender = async (claim, payload, config) => {
  if (!providerOrigin(claim.endpoint)) throw new Error('invalid_push_endpoint');
  const request = await buildPushPayload({ data:JSON.stringify(payload), options:{ttl:claim.ttl} },
    { endpoint:claim.endpoint,expirationTime:null,keys:{p256dh:claim.p256dh,auth:claim.auth} },config);
  return fetch(claim.endpoint,{...request,redirect:'error',signal:AbortSignal.timeout(10000)});
};

export async function drainWebPush({ config, store, send = sendWebPush }: {
  config: PushConfig; store: DeliveryStore; send?: Sender;
}) {
  const ready = pushConfiguration(config);
  if (!ready) return { state:'disabled',attempted:0,accepted:0 };
  await store.enqueue();
  const claims = (await store.claim()).slice(0,20);
  let cursor = 0, attempted = 0, accepted = 0, stopped = false;
  async function consume() {
    if (!ready) return;
    while (!stopped && cursor < claims.length) {
      const claim = claims[cursor++];
      if (!await store.authorize(claim, ready.generation)) continue;
      attempted++;
      let status = 400, retry = 0;
      if (providerOrigin(claim.endpoint)) {
        try {
          const response = await send(claim,{
            schema_version:'partner-needed-event.v1',event_id:claim.event_id,card_item_id:claim.card_item_id,
            path:`/inbox/card/${encodeURIComponent(claim.card_item_id)}`,
          },ready);
          status = response.status;
          retry = retryAfterSeconds(response.headers.get('Retry-After'));
          await response.body?.cancel();
        } catch { status = 0; }
      }
      const recorded = await store.finish(claim,status,retry);
      if (recorded && status >= 200 && status < 300) accepted++;
    }
  }
  const guarded = async () => {
    try { await consume(); }
    catch { stopped = true; throw new Error('web_push_storage_unavailable'); }
  };
  const outcomes = await Promise.allSettled([guarded(),guarded()]);
  if (outcomes.some(result => result.status === 'rejected')) throw new Error('web_push_drain_failed');
  return {state:'drained',attempted,accepted};
}

function isClaim(value: unknown): value is Claim {
  if (typeof value !== 'object' || !value) return false;
  return 'event_id' in value && typeof value.event_id==='string'
    && 'subscription_id' in value && typeof value.subscription_id==='string'
    && 'claim_token' in value && typeof value.claim_token==='string'
    && 'card_item_id' in value && typeof value.card_item_id==='string'
    && 'endpoint' in value && typeof value.endpoint==='string'
    && 'p256dh' in value && typeof value.p256dh==='string'
    && 'auth' in value && typeof value.auth==='string'
    && 'key_generation' in value && typeof value.key_generation==='string'
    && 'ttl' in value && typeof value.ttl==='number' && Number.isInteger(value.ttl) && value.ttl>=0 && value.ttl<=86400;
}
export function postgresDeliveryStore(db: Db): DeliveryStore {
  const call = async (sql: string, args: unknown[] = []): Promise<unknown> => (await db.query(sql,args)).rows[0]?.result;
  return {
    enqueue:() => call('select ops.enqueue_web_push() result'),
    claim:async () => {
      const result = await call('select ops.claim_web_push() result');
      if (!Array.isArray(result) || !result.every(isClaim)) throw new Error('invalid_push_claim');
      return result;
    },
    authorize:async (c,generation) => await call('select ops.authorize_web_push($1,$2,$3,$4) result',
      [c.event_id,c.subscription_id,c.claim_token,generation]) === true,
    finish:(c,status,retry) => call('select ops.finish_web_push($1,$2,$3,$4,$5) result',
      [c.event_id,c.subscription_id,c.claim_token,status,retry]),
  };
}
