import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { trackLocalDatabaseClients } from "./helpers/local-database-clients.ts";

function fixture() {
  const events: string[] = [];
  const client = Object.assign(new EventEmitter<{ end: [] }>(), {
    async end() {},
    connection: { stream: { destroy: () => events.push("socket destroyed") } },
  });
  const pool = Object.assign(new EventEmitter<{ connect: [typeof client] }>(), {
    async end() { events.push("pool ended"); },
  });
  const clients = trackLocalDatabaseClients();
  clients.watch(pool);
  pool.emit("connect", client);
  return {
    events,
    client,
    close: () => clients.close([pool], async () => { events.push("database stopped"); }),
  };
}

test("database shutdown waits for sockets after the pool ends", async () => {
  const { client, events, close } = fixture();
  const closed = close();
  await setImmediate();
  assert.deepEqual(events, ["pool ended"]);

  events.push("socket closed");
  client.emit("end");
  await closed;
  assert.deepEqual(events, ["pool ended", "socket closed", "database stopped"]);
});

test("database shutdown destroys a stuck socket and reports the timeout", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { events, close } = fixture();
  const closed = assert.rejects(close(), /PostgreSQL clients did not close/);
  t.mock.timers.tick(10_000);
  await closed;
  assert.deepEqual(events, ["pool ended", "socket destroyed", "database stopped"]);
});
