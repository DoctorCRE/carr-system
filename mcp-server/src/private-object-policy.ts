// Bound private-object dependency work while retaining permits for unsettled reads.
export const DELIVERABLE_IO_POLICY = Object.freeze({
  timeoutMs: 10_000,
  failureThreshold: 3,
  cooldownMs: 30_000,
  maxOutstanding: 4,
  requestsPerMinute: 60,
});
type Policy = { admit(slug: string): void; run<T>(work: () => Promise<T>): Promise<T> };
const bucketPolicies = new WeakMap<object, Policy>();
function refuse(error: string): never {
  throw Object.assign(new Error(error), { payload: { error } });
}
async function deadline<T>(work: Promise<T>, milliseconds: number, code: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      work,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(Object.assign(new Error(code), { payload: { error: code } })), milliseconds);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
export function dependencyPolicy(bucket: object, timeoutMs: number): Policy {
  const existing = bucketPolicies.get(bucket);
  if (existing) return existing;
  let failures = 0,
    openUntil = 0,
    outstanding = 0,
    probing = false;
  const windows = new Map<string, { minute: number; count: number }>();
  const policy: Policy = {
    admit(slug: string) {
      const minute = Math.floor(Date.now() / 60_000);
      const entry = windows.get(slug);
      const count = entry?.minute === minute ? entry.count + 1 : 1;
      windows.set(slug, { minute, count });
      if (count > DELIVERABLE_IO_POLICY.requestsPerMinute) refuse("deliverable_rate_limited");
    },
    async run<T>(work: () => Promise<T>): Promise<T> {
      if (Date.now() < openUntil || probing || outstanding >= DELIVERABLE_IO_POLICY.maxOutstanding)
        refuse("deliverable_storage_unavailable");
      const probe = openUntil !== 0;
      if (probe) probing = true;
      outstanding++;
      const pending = Promise.resolve()
        .then(work)
        .finally(() => {
          outstanding--;
          if (probe) probing = false;
        });
      try {
        const result = await deadline(pending, timeoutMs, "deliverable_storage_timeout");
        failures = 0;
        openUntil = 0;
        return result;
      } catch (error) {
        if (++failures >= DELIVERABLE_IO_POLICY.failureThreshold || probe) {
          openUntil = Date.now() + DELIVERABLE_IO_POLICY.cooldownMs;
          console.error(
            JSON.stringify({
              event: "deal_deliverables_storage_circuit_open",
              cooldown_ms: DELIVERABLE_IO_POLICY.cooldownMs,
            }),
          );
        }
        throw error;
      }
    },
  };
  bucketPolicies.set(bucket, policy);
  return policy;
}

export async function boundedBody(
  request: { body: ReadableStream<Uint8Array> | null },
  max: number,
  timeoutMs: number,
) {
  const reader = request.body?.getReader();
  if (!reader) refuse("upload_size_refused");
  const expires = Date.now() + timeoutMs;
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      if (Date.now() >= expires) refuse("deliverable_body_timeout");
      const { done, value } = await deadline(
        reader.read(),
        Math.max(1, expires - Date.now()),
        "deliverable_body_timeout",
      );
      if (done) break;
      size += value.byteLength;
      if (size > max) {
        void reader.cancel().catch(() => {});
        refuse("upload_size_refused");
      }
      chunks.push(value);
    }
  } catch (error) {
    void reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}
