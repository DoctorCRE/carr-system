import assert from "node:assert/strict";
import { test } from "node:test";
import { boundedBody } from "../src/private-object-policy.ts";
import { createTourDownloadPolicy } from "../src/tour-download-policy.ts";

test("stalled storage body is cancelled at its deadline", async () => {
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    cancel() {
      cancelled = true;
    },
  });
  await assert.rejects(boundedBody({ body }, 1024, 10), /timeout/);
  assert.equal(cancelled, true);
});

test("oversized storage body refuses bytes and cancels the stream", async () => {
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array(1025));
    },
    cancel() {
      cancelled = true;
    },
  });
  await assert.rejects(boundedBody({ body }, 1024, 1000), /size_refused/);
  assert.equal(cancelled, true);
});

function stalled() {
  let release: () => void = () => {
    throw Error("not initialized");
  };
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

test("grant concurrency refuses request 3 and keeps timed-out permits until settlement", async () => {
  const policy = createTourDownloadPolicy({}, 20);
  const block = stalled();
  let reads = 0;
  const work = () =>
    policy.run(undefined, async (guard) => {
      guard.admitGrant("grant-a");
      reads++;
      await block.promise;
      guard.assertActive();
    });
  const first = work();
  const second = work();
  await assert.rejects(work(), /download_rate_limited/);
  await assert.rejects(first, /timeout/);
  await assert.rejects(second, /timeout/);
  await assert.rejects(work(), /download_rate_limited/);
  assert.equal(reads, 2);
  block.release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await policy.run(undefined, async (guard) => {
    guard.admitGrant("grant-a");
  });
});

test("overall workload refuses request 5 across different grants", async () => {
  const policy = createTourDownloadPolicy({}, 1000);
  const block = stalled();
  const work = (id: string) =>
    policy.run(undefined, async (guard) => {
      guard.admitGrant(id);
      await block.promise;
    });
  const pending = ["a", "b", "c", "d"].map(work);
  await assert.rejects(work("e"), /unavailable/);
  block.release();
  await Promise.all(pending);
});

test("grant minute budget refuses request 11 and expires", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"] });
  const policy = createTourDownloadPolicy({});
  for (let n = 0; n < 10; n++)
    await policy.run(undefined, async (guard) => {
      guard.admitGrant("grant");
    });
  await assert.rejects(
    policy.run(undefined, async (guard) => {
      guard.admitGrant("grant");
    }),
    /download_rate_limited/,
  );
  t.mock.timers.tick(60_001);
  await policy.run(undefined, async (guard) => {
    guard.admitGrant("grant");
  });
});

test("overall minute budget refuses request 61 before dependency work", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"] });
  const policy = createTourDownloadPolicy({});
  let reads = 0;
  for (let n = 0; n < 60; n++)
    await policy.run(undefined, async () => {
      reads++;
    });
  await assert.rejects(
    policy.run(undefined, async () => {
      reads++;
    }),
    /rate_limited/,
  );
  assert.equal(reads, 60);
  t.mock.timers.tick(60_001);
  await policy.run(undefined, async () => {
    reads++;
  });
  assert.equal(reads, 61);
});

test("three dependency failures open circuit; cooldown admits only one recovery probe", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"] });
  const alerts: string[] = [];
  t.mock.method(console, "error", (value: string) => {
    alerts.push(value);
  });
  const policy = createTourDownloadPolicy({});
  for (let n = 0; n < 3; n++)
    await assert.rejects(
      policy.run(undefined, async () => {
        throw Error("storage failed");
      }),
    );
  assert.deepEqual(
    alerts.map((value) => JSON.parse(value)),
    [
      {
        event: "deal_deliverables_storage_circuit_open",
        cooldown_ms: 30_000,
      },
    ],
  );
  let reads = 0;
  const work = async () => {
    reads++;
  };
  await assert.rejects(policy.run(undefined, work), /unavailable/);
  assert.equal(reads, 0);
  t.mock.timers.tick(30_001);
  const block = stalled();
  const probe = policy.run(undefined, async () => {
    await block.promise;
  });
  await assert.rejects(policy.run(undefined, work), /unavailable/);
  block.release();
  await probe;
  await policy.run(undefined, work);
  assert.equal(reads, 1);
});

test("abort returns promptly and late acquisition cannot start body reads", async () => {
  const policy = createTourDownloadPolicy({});
  const block = stalled();
  const controller = new AbortController();
  let bodies = 0;
  const pending = policy.run(controller.signal, async (guard) => {
    await block.promise;
    guard.assertActive();
    bodies++;
  });
  controller.abort();
  await assert.rejects(pending, /aborted/);
  block.release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(bodies, 0);
});

test("timed-out recovery probe stays exclusive until its underlying read settles", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"] });
  t.mock.method(console, "error", () => {});
  const policy = createTourDownloadPolicy({}, 100);
  for (let n = 0; n < 3; n++)
    await assert.rejects(
      policy.run(undefined, async () => {
        throw Error("storage");
      }),
    );
  t.mock.timers.tick(30_001);
  const block = stalled();
  try {
    const probe = assert.rejects(
      policy.run(undefined, async () => {
        await block.promise;
      }),
      /timeout/,
    );
    t.mock.timers.tick(101);
    await probe;
    t.mock.timers.tick(30_001);
    await assert.rejects(
      policy.run(undefined, async () => {}),
      /unavailable/,
    );
  } finally {
    block.release();
  }
});
