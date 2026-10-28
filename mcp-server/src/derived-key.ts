// One deterministic UUID per (namespace, value). A nested or scheduled write
// derives its idempotency_key from the parent key or incident it belongs to, so
// a replay of the parent replays the child; the envelope accepts only UUIDs.
import { sha256 } from "./sha256.ts";

export function derivedIdempotencyKey(namespace: string, value: string): string {
  const hex = sha256(`${namespace}:${value}`).slice(0, 32).split("");
  hex[12] = "5";
  hex[16] = "89ab"[parseInt(hex[16], 16) & 3];
  const h = hex.join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
