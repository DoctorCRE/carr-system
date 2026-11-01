// Observe the real clients used by child suites, independently of fixture metadata.
import assert from 'node:assert/strict';
import { appendFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import childProcess from 'node:child_process';
import pg from 'pg';

if (process.env.WR623_PG_RECORD) {
  if (process.env.WR623_STALE_SNAPSHOT_KEY) {
    const spawn = childProcess.spawn;
    childProcess.spawn = function(command, args, options) {
      const index = args?.indexOf('--snapshot-key') ?? -1;
      if (args?.some(arg => String(arg).endsWith('/lib/disposable_pg_fixture.py')) && index >= 0) {
        args = [...args];
        // Negative control: falsely label the old snapshot as current, disabling pickup.
        args[index + 1] = process.env.WR623_STALE_SNAPSHOT_KEY;
      }
      return spawn.call(this, command, args, options);
    };
    syncBuiltinESMExports();
  }

  if (process.env.WR623_DELAY_POOL_CLOSE === '1') {
    const end = pg.Client.prototype.end;
    pg.Client.prototype.end = function(callback) {
      // Pool.end resolves before these callbacks: model a slower TCP teardown.
      if (typeof callback === 'function') {
        setTimeout(() => end.call(this, callback), 150);
        return;
      }
      return end.call(this);
    };
  }

  const connect = pg.Client.prototype.connect;
  const query = pg.Client.prototype.query;
  async function observe(client) {
    const identity = (await query.call(client, 'select current_database() database, inet_server_port() port')).rows[0];
    if (process.env.WR623_PROBE_COLUMN) {
      identity.column = (await query.call(client, "select exists(select from information_schema.columns where table_schema='public' and table_name='actor' and column_name='wr623_snapshot_probe') present")).rows[0].present;
    }
    appendFileSync(process.env.WR623_PG_RECORD, JSON.stringify(identity) + '\n');
    if (process.env.WR623_EXPECT_PREFIX) {
      const base = new URL(process.env.CARR_CI_DATABASE_URL);
      const prefix = process.env.WR623_EXPECT_PREFIX.replace(/[^a-z0-9_]/g, '_').replace(/^_+|_+$/g, '').slice(0, 28);
      assert.notEqual(identity.database, decodeURIComponent(base.pathname.slice(1)), 'child suite connected to the shared service database');
      assert.notEqual(identity.database, 'carr_ci', 'child suite connected to carr_ci');
      assert.match(identity.database, new RegExp(`^${prefix}_[a-f0-9]{32}$`), 'child suite must use its own database');
      assert.equal(identity.port, Number(base.port || 5432), 'child suite must use the provided service');
    }
    if (process.env.WR623_PROBE_COLUMN)
      assert.equal(identity.column, true, 'scratch migration missing from child suite database');
  }
  pg.Client.prototype.connect = function(callback) {
    if (process.env.WR623_REDIRECT_TO_SHARED) {
      this.database = new URL(process.env.CARR_CI_DATABASE_URL).pathname.slice(1);
      this.connectionParameters.database = this.database;
    }
    if (typeof callback === 'function')
      return connect.call(this, error => error ? callback(error) : observe(this).then(() => callback(), callback));
    return connect.call(this).then(() => observe(this));
  };
}
