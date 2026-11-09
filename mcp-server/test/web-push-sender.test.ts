import assert from "node:assert/strict";
import { generateKeyPairSync, randomBytes } from "node:crypto";
import test from "node:test";
import { drainPushOutbox, scheduledPushOutbox } from "../src/web-push-outbox.ts";
import { submitPush } from "../src/web-push-sender.ts";

const vapid = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
const jwk = vapid.privateKey.export({ format: "jwk" });
const device = generateKeyPairSync("ec", { namedCurve: "prime256v1" }).publicKey.export({ format: "jwk" });
function point(key: JsonWebKey) {
  return Buffer.concat([
    Buffer.from([4]),
    Buffer.from(key.x ?? "", "base64url"),
    Buffer.from(key.y ?? "", "base64url"),
  ]).toString("base64url");
}
const keys = { publicKey: point(jwk), privateKey: jwk.d ?? "", subject: "mailto:push@example.invalid" };
const delivery = {
  endpoint: "https://web.push.apple.com/synthetic",
  p256dh: point(device),
  auth: randomBytes(16).toString("base64url"),
  notification_id: "20000000-0000-4000-8000-000000000001",
  target_ref: "al1_synthetic",
  ttl: 60,
};
test("controlled HTTP outcomes use bounded encrypted requests and no delivery claim", async () => {
  for (const [status, outcome] of [
    [201, "provider_accepted"],
    [400, "failed"],
    [401, "auth"],
    [403, "auth"],
    [404, "gone"],
    [410, "gone"],
    [429, "retry"],
    [500, "retry"],
    [302, "failed"],
  ] as const) {
    const result = await submitPush(delivery, keys, 100, async (url, init) => {
      assert.equal(url, delivery.endpoint);
      assert.equal(init?.redirect, "error");
      assert.ok(init?.signal);
      assert.ok(init?.body instanceof Uint8Array);
      assert.equal(new Headers(init?.headers).get("content-encoding"), "aes128gcm");
      assert.ok(!Buffer.from(init.body).includes(Buffer.from("al1_synthetic")));
      return new Response(null, { status, headers: { "Retry-After": "300" } });
    });
    assert.equal(result.outcome, outcome);
    assert.equal(result.retry_after, 300);
  }
});
test("connection loss and timeout stay unknown", async () => {
  assert.equal(
    (
      await submitPush(delivery, keys, 50, async () => {
        throw new TypeError("connection lost");
      })
    ).outcome,
    "unknown",
  );
  assert.equal(
    (
      await submitPush(
        delivery,
        keys,
        10,
        async (_url, init) =>
          new Promise((_resolve, reject) =>
            init?.signal?.addEventListener("abort", () => reject(new Error("timeout"))),
          ),
      )
    ).outcome,
    "unknown",
  );
});
test("untrusted destinations never reach fetch", async () => {
  for (const endpoint of [
    "http://127.0.0.1/x",
    "https://web.push.apple.com:444/x",
    "https://web.push.apple.com@evil.invalid/x",
  ])
    assert.equal(
      (
        await submitPush({ ...delivery, endpoint }, keys, 100, async () => {
          throw new Error("must not fetch");
        })
      ).outcome,
      "failed",
    );
});
test("database failure and disabled scheduler submit nothing", async () => {
  let sends = 0;
  await assert.rejects(
    drainPushOutbox(
      {
        query: async () => {
          throw new Error("database unavailable");
        },
      },
      keys,
      async () => {
        sends++;
        return new Response(null, { status: 201 });
      },
    ),
  );
  await scheduledPushOutbox({ DATABASE_URL_WRITER: "invalid-fixture-dsn" });
  assert.equal(sends, 0);
});
