// Bound client PDF downloads by grant and shared private-object capacity.
import { DELIVERABLE_IO_POLICY, dependencyPolicy } from "./private-object-policy.ts";

type Guard = { admitGrant(id: string): void; assertActive(): void };
type GrantBudget = { minute: number; count: number; outstanding: number };
const TOUR_DOWNLOAD_POLICY = Object.freeze({ perGrant: 2, perMinute: 10, trackedGrants: 1024 });
const policies = new WeakMap<object, ReturnType<typeof createTourDownloadPolicy>>();
const refusal = (code: string) => Object.assign(new Error(code), { downloadRefusal: true });

export function createTourDownloadPolicy(bucket: object, timeoutMs: number = DELIVERABLE_IO_POLICY.timeoutMs) {
  const dependency = dependencyPolicy(bucket, timeoutMs);
  const grants = new Map<string, GrantBudget>();
  return {
    async run<T>(signal: AbortSignal | undefined, work: (guard: Guard) => Promise<T>): Promise<T> {
      if (signal?.aborted) throw refusal("download_aborted");
      dependency.admit("tour-download");
      let active = true;
      let grant: GrantBudget | undefined;
      let abort: (() => void) | undefined;
      const assertActive = () => {
        if (!active || signal?.aborted) throw refusal("download_aborted");
      };
      const pending = dependency.run(async () => {
        try {
          const value = await work({
            assertActive,
            admitGrant(id) {
              assertActive();
              const minute = Math.floor(Date.now() / 60_000);
              for (const [key, entry] of grants)
                if (entry.minute !== minute && entry.outstanding === 0) grants.delete(key);
              let entry = grants.get(id);
              if (!entry) {
                if (grants.size >= TOUR_DOWNLOAD_POLICY.trackedGrants) throw refusal("download_rate_limited");
                entry = { minute, count: 0, outstanding: 0 };
                grants.set(id, entry);
              }
              if (entry.minute !== minute) {
                entry.minute = minute;
                entry.count = 0;
              }
              if (entry.count >= TOUR_DOWNLOAD_POLICY.perMinute || entry.outstanding >= TOUR_DOWNLOAD_POLICY.perGrant)
                throw refusal("download_rate_limited");
              entry.count++;
              entry.outstanding++;
              grant = entry;
            },
          });
          assertActive();
          return { ok: true as const, value };
        } catch (error) {
          if (error instanceof Error && "downloadRefusal" in error) return { ok: false as const, error };
          throw error;
        } finally {
          if (grant) grant.outstanding--;
        }
      });
      try {
        const result = await Promise.race([
          pending,
          new Promise<never>((_, reject) => {
            abort = () => reject(refusal("download_aborted"));
            signal?.addEventListener("abort", abort, { once: true });
            if (signal?.aborted) abort();
          }),
        ]);
        if (!result.ok) throw result.error;
        return result.value;
      } finally {
        active = false;
        if (abort) signal?.removeEventListener("abort", abort);
      }
    },
  };
}

export function tourDownloadPolicy(bucket: object) {
  let policy = policies.get(bucket);
  if (!policy) {
    policy = createTourDownloadPolicy(bucket);
    policies.set(bucket, policy);
  }
  return policy;
}
