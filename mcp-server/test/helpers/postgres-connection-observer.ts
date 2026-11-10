// Observe the real clients used by child suites, independently of fixture metadata.
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import { appendFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import pg from "pg";

const record = process.env.WR623_PG_RECORD;
if (record) {
  const recordPath = record;
  if (process.env.WR623_STALE_SNAPSHOT_KEY) {
    childProcess.spawn = new Proxy(childProcess.spawn, {
      apply(spawn, receiver, parameters: unknown[]) {
        const [, args] = parameters;
        const index = Array.isArray(args) ? args.indexOf("--snapshot-key") : -1;
        if (
          Array.isArray(args) &&
          args.some((arg) => String(arg).endsWith("/lib/disposable_pg_fixture.py")) &&
          index >= 0
        ) {
          const rewritten = [...args];
          // Negative control: falsely label the old snapshot as current, disabling pickup.
          rewritten[index + 1] = process.env.WR623_STALE_SNAPSHOT_KEY;
          parameters[1] = rewritten;
        }
        return Reflect.apply(spawn, receiver, parameters);
      },
    });
    syncBuiltinESMExports();
  }

  if (process.env.WR623_DELAY_POOL_CLOSE === "1") {
    const endAsync: (this: pg.Client) => Promise<void> = pg.Client.prototype.end;
    const endCallback: (this: pg.Client, callback: (err: Error) => void) => void = pg.Client.prototype.end;
    function delayedEnd(this: pg.Client): Promise<void>;
    function delayedEnd(this: pg.Client, callback: (err: Error) => void): void;
    function delayedEnd(this: pg.Client, callback?: (err: Error) => void): Promise<void> | undefined {
      // Pool.end resolves before these callbacks: model a slower TCP teardown.
      if (typeof callback === "function") {
        setTimeout(() => endCallback.call(this, callback), 150);
        return;
      }
      return endAsync.call(this);
    }
    pg.Client.prototype.end = delayedEnd;
  }

  const connectAsync: (this: pg.Client) => Promise<pg.Client> = pg.Client.prototype.connect;
  const connectCallback: (this: pg.Client, callback: (err: Error | null) => void) => void = pg.Client.prototype.connect;
  const query: (this: pg.Client, text: string) => Promise<pg.QueryResult<Record<string, unknown>>> =
    pg.Client.prototype.query;
  async function observe(client: pg.Client, record: string) {
    const rows: unknown[] = (await query.call(client, "select current_database() database, inet_server_port() port"))
      .rows;
    const row = rows[0];
    if (
      typeof row !== "object" ||
      row === null ||
      !("database" in row) ||
      typeof row.database !== "string" ||
      !("port" in row) ||
      (row.port !== null && typeof row.port !== "number")
    )
      throw new Error("invalid PostgreSQL identity");
    const identity: { database: string; port: number | null; column?: boolean } = {
      database: row.database,
      port: row.port,
    };
    if (process.env.WR623_PROBE_COLUMN) {
      const probe: unknown = (
        await query.call(
          client,
          "select exists(select from information_schema.columns where table_schema='public' and table_name='actor' and column_name='wr623_snapshot_probe') present",
        )
      ).rows[0];
      if (typeof probe !== "object" || probe === null || !("present" in probe) || typeof probe.present !== "boolean")
        throw new Error("invalid PostgreSQL column probe");
      identity.column = probe.present;
    }
    appendFileSync(record, `${JSON.stringify(identity)}\n`);
    if (process.env.WR623_EXPECT_PREFIX) {
      const base = new URL(process.env.CARR_CI_DATABASE_URL ?? "");
      const prefix = process.env.WR623_EXPECT_PREFIX.replace(/[^a-z0-9_]/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 28);
      assert.notEqual(
        identity.database,
        decodeURIComponent(base.pathname.slice(1)),
        "child suite connected to the shared service database",
      );
      assert.notEqual(identity.database, "carr_ci", "child suite connected to carr_ci");
      assert.match(identity.database, new RegExp(`^${prefix}_[a-f0-9]{32}$`), "child suite must use its own database");
      assert.equal(identity.port, Number(base.port || 5432), "child suite must use the provided service");
    }
    if (process.env.WR623_PROBE_COLUMN)
      assert.equal(identity.column, true, "scratch migration missing from child suite database");
  }
  type ConnectCallback = Parameters<pg.Client["connect"]>[0];
  function observedConnect(this: pg.Client): Promise<pg.Client>;
  function observedConnect(this: pg.Client, callback: ConnectCallback): void;
  function observedConnect(this: pg.Client, callback?: ConnectCallback): Promise<pg.Client> | undefined {
    if (process.env.WR623_REDIRECT_TO_SHARED) {
      this.database = new URL(process.env.CARR_CI_DATABASE_URL ?? "").pathname.slice(1);
      if (
        !("connectionParameters" in this) ||
        typeof this.connectionParameters !== "object" ||
        this.connectionParameters === null
      )
        throw new Error("missing PostgreSQL connection parameters");
      Object.assign(this.connectionParameters, { database: this.database });
    }
    if (typeof callback === "function") {
      connectCallback.call(this, (error: Error | null) => {
        if (error) Reflect.apply(callback, undefined, [error]);
        else
          void observe(this, recordPath).then(
            () => Reflect.apply(callback, undefined, []),
            (error) => Reflect.apply(callback, undefined, [error]),
          );
      });
      return;
    }
    const connected = connectAsync.call(this);
    return connected.then(async (client) => {
      await observe(this, recordPath);
      return client;
    });
  }
  pg.Client.prototype.connect = observedConnect;
}
