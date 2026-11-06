import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import pg from "pg";
import { executeRegisteredTool } from "../src/tools.js";
import { restoreEventIdentity } from "./helpers/snapshot-schema.mjs";
import { postgresDatabase } from "./helpers/postgres-database.mjs";
import {
  dealDeliverableTools,
  DELIVERABLE_POLICY,
  DELIVERABLE_PREFIX,
} from "../src/deal-deliverables.js";
import { createDealDeliverablesHandler } from "../src/deal-deliverables-web.js";
const actor = { slug: "joe", human: true };
const deal = randomUUID(),
  otherDeal = randomUUID();
const bytes = new TextEncoder().encode("%PDF-1.7\nSynthetic file\n%%EOF");
const sha256 = createHash("sha256").update(bytes).digest("hex");
const input = {
  deal_id: deal,
  filename: "Synthetic.pdf",
  bytes: bytes.length,
  mime: "application/pdf",
  sha256,
};

test("PostgreSQL deliverable lifecycle and guarded byte transport", async (t) => {
  const db = await postgresDatabase("deal-deliverables-");
  const c = db.client;
  const events = [];
  const tools = dealDeliverableTools({
    withEnvelope: async (_c, _a, _n, _args, fn) => fn(),
    writeEvent: async (...args) => events.push(args),
  });
  const transaction = async (fn) => {
    await c.query("begin");
    try {
      const result = await fn(c);
      await c.query("commit");
      return result;
    } catch (e) {
      await c.query("rollback");
      throw e;
    }
  };
  const call = (verb, args, who = actor) =>
    transaction((c) =>
      tools[verb].handler(c, who, { idempotency_key: randomUUID(), ...args }),
    );
  const objects = new Map();
  let revoked = false,
    revokeOnPut = false,
    revokeOnGet = false;
  const bucket = {
    async put(key, b, opts) {
      objects.set(key, { bytes: b, ...opts });
      if (revokeOnPut) revoked = true;
      return { key };
    },
    async delete(key) {
      objects.delete(key);
    },
    async get(key) {
      if (revokeOnGet) revoked = true;
      const o = objects.get(key);
      return (
        o && {
          body: o.bytes,
          size: o.bytes.length,
          customMetadata: o.customMetadata,
        }
      );
    },
  };
  const handler = createDealDeliverablesHandler({
    transaction,
    bucket,
    callTool: async (w, v, a, active) => {
      await active();
      return call(v, a, w);
    },
  });
  const request = (path, method = "GET", body, headers = {}) =>
    new Request(`https://example.test${path}`, { method, body, headers });
  const put = (id, b = bytes, who = actor) =>
    handler(
      request(`${DELIVERABLE_PREFIX}/${deal}/${id}/bytes`, "PUT", b, {
        "content-type": "application/octet-stream",
      }),
      who,
      async () => !revoked,
    );
  const download = (id) =>
    handler(
      request(`${DELIVERABLE_PREFIX}/${deal}/${id}/download`),
      actor,
      async () => !revoked,
    );
  const start = (args = {}) =>
    call("start-deal-deliverable", { ...input, ...args });
  const finalize = (id, args = {}) =>
    call("finalize-deal-deliverable", {
      deal_id: deal,
      deliverable_id: id,
      ...args,
    });
  const visibility = (id, expected_version, value = "shared") =>
    call("set-deal-deliverable-visibility", {
      deal_id: deal,
      deliverable_id: id,
      expected_version,
      visibility: value,
    });
  const reject = (p, code) =>
    assert.rejects(p, (e) => e.payload?.error === code);
  try {
    await c.query(`create schema ops; create role carr_writer; create role carr_reader;
      create function ops.scac_current_grant_contract() returns jsonb language sql immutable as $$select '{"relation_dml":[]}'::jsonb$$;
      create table actor(id uuid default gen_random_uuid(),slug text,kind text default 'human',active boolean); insert into actor(slug,active) values('joe',true),('dell',true);
      create table party(id uuid primary key,merged_into uuid,deleted_at timestamptz);
      create table client(id uuid primary key,party_id uuid,merged_into uuid);
      create table deal(id uuid primary key,client_id uuid,owner text);
      insert into party(id) values('${deal}');insert into client(id,party_id) values('${deal}','${deal}');
      insert into deal values('${deal}','${deal}','joe'),('${otherDeal}','${deal}','dell')`);
    const schema = await readFile(
      new URL("../../db/schema.sql", import.meta.url),
      "utf8",
    );
    const resolver = schema.match(
      /CREATE FUNCTION public\.retrieval_visibility_actor_id\(p_sponsor_slug text\)[\s\S]*?\$\$;/,
    );
    assert.ok(resolver);
    await c.query(resolver[0]);
    await c.query(
      "grant usage on schema public,ops to carr_reader; grant select on deal,client,party to carr_reader; grant select(id,slug) on actor to carr_reader",
    );
    await c.query(
      await readFile(
        new URL("../../migrations/1158_deal_deliverables.sql", import.meta.url),
        "utf8",
      ),
    );
    await t.test(
      "reader authorization works without access to actor.active and refuses inactive partners",
      async () => {
        try {
          await c.query("set role carr_reader");
          const result = await tools["list-deal-deliverables"].handler(
            c,
            actor,
            { deal_id: deal },
          );
          assert.equal(result.ok, true);
          assert.deepEqual(result.deliverables, []);
        } finally {
          await c.query("reset role");
        }
        await c.query("update actor set active=false where slug='joe'");
        try {
          await c.query("set role carr_reader");
          await reject(
            tools["list-deal-deliverables"].handler(c, actor, {
              deal_id: deal,
            }),
            "deliverable_denied",
          );
        } finally {
          await c.query("reset role");
          await c.query("update actor set active=true where slug='joe'");
        }
      },
    );
    await t.test(
      "migration registers its exact writer grants in the current capability contract",
      async () => {
        const contract = (
          await c.query("select ops.scac_current_grant_contract() contract")
        ).rows[0].contract;
        assert.deepEqual(
          contract.relation_dml
            .filter((row) => row.relation === "ops.deal_deliverable")
            .map((row) => row.privilege)
            .sort(),
          ["insert", "update"],
        );
      },
    );
    await t.test(
      "concurrent reservations cannot exceed the file quota",
      async () => {
        const isolatedDeal = randomUUID();
        await c.query("insert into deal values($1,$2,'joe')", [
          isolatedDeal,
          deal,
        ]);
        await c.query(
          `insert into ops.deal_deliverable(id,organization_tenant_id,deal_id,filename,mime,bytes,sha256,object_key,created_by)
        select gen_random_uuid(),'carr-internal',$1,'reserved.pdf','application/pdf',1,$2,'isolated/'||n,'joe'
        from generate_series(1,99)n`,
          [isolatedDeal, sha256],
        );
        const peer = new pg.Client(c.connectionParameters);
        await peer.connect();
        try {
          await peer.query("begin");
          await tools["start-deal-deliverable"].handler(peer, actor, {
            ...input,
            deal_id: isolatedDeal,
            idempotency_key: randomUUID(),
          });
          await c.query("set lock_timeout='100ms'");
          await assert.rejects(
            start({ deal_id: isolatedDeal.toUpperCase() }),
            (error) => error.code === "55P03",
          );
          await c.query("set lock_timeout=0");
          await peer.query("commit");
          await reject(
            start({ deal_id: isolatedDeal }),
            "deliverable_quota_exceeded",
          );
          assert.equal(
            (
              await c.query(
                "select count(*)::int n from ops.deal_deliverable where deal_id=$1",
                [isolatedDeal],
              )
            ).rows[0].n,
            100,
          );
        } finally {
          await peer.end();
        }
      },
    );
    await t.test(
      "concurrent finalization returns one new file and one existing file",
      async () => {
        const pending = (await start()).deliverable.id;
        assert.equal((await put(pending)).status, 201);
        const peer = new pg.Client(c.connectionParameters);
        await peer.connect();
        try {
          await peer.query("begin");
          const first = await tools["finalize-deal-deliverable"].handler(
            peer,
            actor,
            {
              deal_id: deal,
              deliverable_id: pending,
              idempotency_key: randomUUID(),
            },
          );
          const waiting = finalize(pending);
          await peer.query("commit");
          assert.equal(first.existing, false);
          assert.equal((await waiting).existing, true);
          await c.query("delete from ops.deal_deliverable where id=$1", [
            pending,
          ]);
        } finally {
          await peer.end();
        }
      },
    );
    await t.test(
      "UUID spelling cannot change the quota lock or stored object namespace",
      async () => {
        const started = await start({ deal_id: deal.toUpperCase() });
        assert.equal(
          started.upload_path,
          `${DELIVERABLE_PREFIX}/${deal}/${started.deliverable.id}/bytes`,
        );
        assert.equal((await put(started.deliverable.id)).status, 201);
      },
    );
    await t.test(
      "dependency deadlines release row locks and late PUT cannot delete a retry",
      async () => {
        const pending = (await start()).deliverable.id;
        let release;
        const late = new Promise((resolve) => {
          release = resolve;
        });
        const slow = createDealDeliverablesHandler({
          transaction,
          bucket: {
            ...bucket,
            put: async (...args) => {
              await late;
              return bucket.put(...args);
            },
          },
          operationTimeoutMs: 20,
        });
        const response = await Promise.race([
          slow(
            request(
              `${DELIVERABLE_PREFIX}/${deal}/${pending}/bytes`,
              "PUT",
              bytes,
              { "content-type": "application/octet-stream" },
            ),
            actor,
            async () => true,
          ),
          new Promise((resolve) => setTimeout(() => resolve(null), 300)),
        ]);
        if (!response) release();
        assert.equal(response?.status, 503, "stalled PUT must time out");
        assert.equal(
          (
            await c.query(
              "select state from ops.deal_deliverable where id=$1",
              [pending],
            )
          ).rows[0].state,
          "started",
        );
        assert.equal((await put(pending)).status, 201);
        release();
        await late;
        await finalize(pending);
        assert.equal((await download(pending)).status, 200);
      },
    );
    await t.test(
      "revoked download refuses every observable lifecycle state",
      async () => {
        for (const state of [
          "absent",
          "started",
          "expired",
          "verified",
          "private",
          "private-downloaded",
          "shared",
          "shared-downloaded",
        ]) {
          const pending =
            state === "absent" ? randomUUID() : (await start()).deliverable.id;
          if (state === "expired") {
            await c.query(
              "update ops.deal_deliverable set expires_at=now()-interval '1 second' where id=$1",
              [pending],
            );
            await start();
          } else if (!["absent", "started"].includes(state)) {
            assert.equal((await put(pending)).status, 201);
            if (state !== "verified") {
              await finalize(pending);
              if (state.startsWith("shared")) await visibility(pending, 1);
              if (state.endsWith("downloaded"))
                assert.equal((await download(pending)).status, 200);
            }
          }
          revoked = true;
          assert.equal((await download(pending)).status, 401, state);
          revoked = false;
        }
      },
    );
    await t.test(
      "hash refusal rolls back and valid retry verifies",
      async () => {
        const pending = (await start({ sha256: "0".repeat(64) })).deliverable
          .id;
        assert.equal((await put(pending)).status, 422);
        assert.equal(
          (
            await c.query(
              "select state from ops.deal_deliverable where id=$1",
              [pending],
            )
          ).rows[0].state,
          "started",
        );
        await c.query("update ops.deal_deliverable set sha256=$2 where id=$1", [
          pending,
          sha256,
        ]);
        assert.equal((await put(pending)).status, 201);
      },
    );
    await t.test(
      "body and download stalls have deadlines without retaining lifecycle locks",
      async () => {
        const pending = (await start()).deliverable.id;
        const limited = createDealDeliverablesHandler({
          transaction,
          bucket: { ...bucket },
          operationTimeoutMs: 20,
        });
        const body = new ReadableStream({ start() {} });
        const response = await limited(
          new Request(
            `https://example.test${DELIVERABLE_PREFIX}/${deal}/${pending}/bytes`,
            {
              method: "PUT",
              body,
              duplex: "half",
              headers: { "content-type": "application/octet-stream" },
            },
          ),
          actor,
          async () => true,
        );
        assert.equal(response.status, 408);
        assert.equal((await put(pending)).status, 201);
        await finalize(pending);
        let release;
        const stalled = new Promise((resolve) => {
          release = resolve;
        });
        const slow = createDealDeliverablesHandler({
          transaction,
          bucket: { get: () => stalled },
          operationTimeoutMs: 20,
        });
        const reply = await slow(
          request(`${DELIVERABLE_PREFIX}/${deal}/${pending}/download`),
          actor,
          async () => true,
        );
        assert.equal(reply.status, 503);
        release(null);
        assert.equal((await download(pending)).status, 200);
      },
    );
    await t.test(
      "visibility changes with and without prior downloads",
      async () => {
        for (const downloaded of [false, true]) {
          const pending = (await start()).deliverable.id;
          assert.equal((await put(pending)).status, 201);
          await finalize(pending);
          if (downloaded) assert.equal((await download(pending)).status, 200);
          assert.equal(
            (await visibility(pending, 1)).deliverable.visibility,
            "shared",
          );
          if (downloaded) assert.equal((await download(pending)).status, 200);
          assert.equal(
            (await visibility(pending, 2, "private")).deliverable.visibility,
            "private",
          );
        }
      },
    );
    let id;
    await t.test(
      "started -> verified -> finalized privately -> shared -> unshared -> download",
      async () => {
        const s = await start();
        id = s.deliverable.id;
        assert.equal(s.deliverable.state, "started");
        assert.equal(s.deliverable.visibility, "private");
        await reject(finalize(id), "deliverable_not_verified");
        assert.equal((await download(id)).status, 409);
        const uploaded = await put(id);
        assert.equal(uploaded.status, 201, await uploaded.clone().text());
        assert.equal((await put(id)).status, 409);
        const f = await finalize(id);
        assert.equal(f.deliverable.visibility, "private");
        assert.equal(f.deliverable.version, 1);
        assert.equal(
          (await visibility(id, 1)).deliverable.visibility,
          "shared",
        );
        assert.equal((await download(id)).status, 200);
        await reject(visibility(id, 1), "version_conflict");
        assert.equal(
          (await visibility(id, 2, "private")).deliverable.version,
          3,
        );
        const d = await download(id);
        assert.equal(d.status, 200);
        assert.deepEqual(new Uint8Array(await d.arrayBuffer()), bytes);
        assert.equal(d.headers.get("cache-control"), "private, no-store");
        assert.equal(d.headers.get("x-content-type-options"), "nosniff");
        const listed = await call("list-deal-deliverables", { deal_id: deal });
        assert.ok(listed.deliverables.some((row) => row.id === id));
        assert.equal("object_key" in listed.deliverables[0], false);
      },
    );
    await t.test(
      "duplicate finalization returns the original without a second event",
      async () => {
        const before = events.length;
        const again = await finalize(id);
        assert.equal(again.existing, true);
        assert.equal(events.length, before);
        assert.equal(again.deliverable.version, 3);
        assert.equal("object_key" in again.deliverable, false);
      },
    );
    await t.test(
      "concurrent visibility writes admit exactly one current version",
      async () => {
        const peer = new pg.Client(c.connectionParameters);
        await peer.connect();
        try {
          const args = {
            deal_id: deal,
            deliverable_id: id,
            expected_version: 3,
            visibility: "shared",
            idempotency_key: randomUUID(),
          };
          await peer.query("begin");
          const first = await tools["set-deal-deliverable-visibility"].handler(
            peer,
            actor,
            args,
          );
          assert.equal(first.deliverable.version, 4);
          const second = visibility(id, 3);
          await peer.query("commit");
          await reject(second, "version_conflict");
          assert.equal(
            (
              await c.query(
                "select version from ops.deal_deliverable where id=$1",
                [id],
              )
            ).rows[0].version,
            4,
          );
        } finally {
          await peer.end();
        }
      },
    );
    await t.test(
      "tenant, deal, actor, forged object key and unknown target refusals",
      async () => {
        await reject(
          finalize(id, { deal_id: otherDeal }),
          "deliverable_denied",
        );
        await reject(finalize(randomUUID()), "deliverable_denied");
        await reject(
          call(
            "list-deal-deliverables",
            { deal_id: deal },
            { slug: "codex", human: false },
          ),
          "deliverable_denied",
        );
        await reject(
          call(
            "list-deal-deliverables",
            { deal_id: deal },
            { slug: "dell", human: true },
          ),
          "deliverable_denied",
        );
        await c
          .query(
            "update ops.deal_deliverable set organization_tenant_id='elsewhere' where id=$1",
            [id],
          )
          .then(
            () => assert.fail("tenant check required"),
            (e) => assert.equal(e.code, "23514"),
          );
        const key = (
          await c.query(
            "select object_key from ops.deal_deliverable where id=$1",
            [id],
          )
        ).rows[0].object_key;
        await c.query(
          "update ops.deal_deliverable set object_key=$2 where id=$1",
          [id, `documents/forged/${id}`],
        );
        assert.equal((await download(id)).status, 403);
        await c.query(
          "update ops.deal_deliverable set object_key=$2 where id=$1",
          [id, key],
        );
      },
    );
    await t.test(
      "size, quota, bad content and hash mismatch do not finalize",
      async () => {
        await reject(
          start({ bytes: DELIVERABLE_POLICY.maxBytes + 1 }),
          "upload_size_refused",
        );
        await reject(start({ mime: "text/html" }), "unsupported_mime");
        const badHash = (await start({ sha256: "0".repeat(64) })).deliverable
          .id;
        assert.equal((await put(badHash)).status, 422);
        await reject(finalize(badHash), "deliverable_not_verified");
        const badType = (await start()).deliverable.id;
        assert.equal(
          (await put(badType, new Uint8Array(bytes.length))).status,
          415,
        );
        assert.equal(
          (await put(badType, new Uint8Array(bytes.length + 1))).status,
          413,
        );
        await c.query("update ops.deal_deliverable set bytes=$2 where id=$1", [
          badType,
          DELIVERABLE_POLICY.maxDealBytes,
        ]);
        await reject(start(), "deliverable_quota_exceeded");
        await c.query("update ops.deal_deliverable set bytes=$2 where id=$1", [
          badType,
          bytes.length,
        ]);
        await c.query(
          "insert into ops.deal_deliverable(id,organization_tenant_id,deal_id,filename,mime,bytes,sha256,object_key,created_by) select gen_random_uuid(),'carr-internal',$1,'quota.pdf','application/pdf',1,$2,'quota/'||n,'joe' from generate_series(1,100)n",
          [deal, sha256],
        );
        await reject(start(), "deliverable_quota_exceeded");
        await c.query(
          "delete from ops.deal_deliverable where object_key like 'quota/%'",
        );
      },
    );
    await t.test(
      "revocation refuses downloads, in-flight upload and in-flight download",
      async () => {
        revoked = true;
        assert.equal((await download(id)).status, 401);
        revoked = false;
        revokeOnGet = true;
        assert.equal((await download(id)).status, 401);
        revokeOnGet = false;
        revoked = false;
        const pending = (await start()).deliverable.id;
        revokeOnPut = true;
        assert.equal((await put(pending)).status, 401);
        revokeOnPut = false;
        revoked = false;
        assert.equal(
          [...objects.keys()].some((k) => k.endsWith(pending)),
          true,
        );
        await reject(finalize(pending), "deliverable_not_verified");
        await c.query("update actor set active=false where slug='joe'");
        assert.equal((await download(id)).status, 403);
        await c.query("update actor set active=true where slug='joe'");
      },
    );
    await t.test(
      "expired uploads refuse bytes and release quota at next reservation",
      async () => {
        const expired = (await start()).deliverable.id;
        await c.query(
          "update ops.deal_deliverable set expires_at=now()-interval '1 second' where id=$1",
          [expired],
        );
        assert.equal((await put(expired)).status, 410);
        await start();
        assert.equal(
          (
            await c.query(
              "select state from ops.deal_deliverable where id=$1",
              [expired],
            )
          ).rows[0].state,
          "expired",
        );
      },
    );
    await t.test(
      "real envelope records partner provenance and rechecks authorization before replay",
      async () => {
        const schema = await readFile(
          new URL("../../db/schema.sql", import.meta.url),
          "utf8",
        );
        for (const name of ["event", "tool_call"]) {
          const ddl = schema.match(
            new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`),
          );
          assert.ok(ddl, name);
          await c.query(ddl[0]);
        }
        await restoreEventIdentity(c, schema);
        await c.query("alter table tool_call add primary key(idempotency_key)");
        const registeredActor = { ...actor, id: randomUUID() };
        const args = { ...input, idempotency_key: randomUUID() };
        const registered = () =>
          transaction((c) =>
            executeRegisteredTool(
              c,
              registeredActor,
              "start-deal-deliverable",
              args,
            ),
          );
        const created = await registered();
        const replayed = await registered();
        assert.equal(replayed.deliverable.id, created.deliverable.id);
        assert.equal(replayed.replayed, true);
        assert.equal(
          (
            await c.query(
              "select cause from event where verb='start-deal-deliverable'",
            )
          ).rows[0].cause,
          "human_stated",
        );
        await c.query("update actor set active=false where slug='joe'");
        await reject(registered(), "deliverable_denied");
      },
    );
    await t.test(
      "production proof CLI accepts real handler receipts and rejects corrupt evidence",
      async () => {
        const { mkdtemp, writeFile, rm } = await import("node:fs/promises");
        const { tmpdir } = await import("node:os");
        const { spawnSync } = await import("node:child_process");
        const directory = await mkdtemp(`${tmpdir()}/J369-proof-`);
        try {
          await c.query("update actor set active=true where slug='joe'");
          const registered = (verb, args) =>
            transaction((c) =>
              executeRegisteredTool(c, { ...actor, id: randomUUID() }, verb, {
                idempotency_key: randomUUID(),
                deal_id: deal,
                ...args,
              }),
            );
          const pending = await registered("start-deal-deliverable", input);
          const upload = await (await put(pending.deliverable.id)).json();
          const target = { deliverable_id: pending.deliverable.id };
          const finalized = await registered(
            "finalize-deal-deliverable",
            target,
          );
          assert.equal((await download(target.deliverable_id)).status, 200);
          const shared = await registered("set-deal-deliverable-visibility", {
            ...target,
            expected_version: 1,
            visibility: "shared",
          });
          assert.equal((await download(target.deliverable_id)).status, 200);
          const unshared = await registered("set-deal-deliverable-visibility", {
            ...target,
            expected_version: 2,
            visibility: "private",
          });
          const data = await (
            await download(target.deliverable_id)
          ).arrayBuffer();
          revoked = true;
          const denied = await download(target.deliverable_id);
          revoked = false;
          const receipt = {
            partner: "joe",
            attestation:
              "Synthetic actual-handler fixture; not production evidence",
            upload,
            finalize: finalized,
            share: shared,
            unshare: unshared,
          };
          const fixture = {
            release: { source_commit: "1".repeat(40) },
            list: await transaction((c) =>
              executeRegisteredTool(c, actor, "list-deal-deliverables", {
                deal_id: deal,
              }),
            ),
            download: { base64: Buffer.from(data).toString("base64") },
            revoked: { status: denied.status },
          };
          await writeFile(`${directory}/receipt.json`, JSON.stringify(receipt));
          await writeFile(`${directory}/fixture.json`, JSON.stringify(fixture));
          const proofPath = fileURLToPath(
            new URL("../../ops/deal-deliverables-proof.py", import.meta.url),
          );
          const command = [
            proofPath,
            "--receipt",
            `${directory}/receipt.json`,
            "--fixture",
            `${directory}/fixture.json`,
            "--sha",
            "1".repeat(40),
          ];
          const pass = spawnSync("python3", command, { encoding: "utf8" });
          assert.equal(pass.status, 0, pass.stdout + pass.stderr);
          await writeFile(`${directory}/active-cookie`, "synthetic-active");
          await writeFile(`${directory}/revoked-cookie`, "synthetic-revoked");
          const transport = spawnSync(
            "python3",
            [
              "-c",
              `
import importlib.util, io, json, base64, sys
from functools import partial
spec = importlib.util.spec_from_file_location('proof', sys.argv[4])
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
fixture = json.load(open(sys.argv[2]))
receipt = json.load(open(sys.argv[1]))
row = receipt['finalize']['deliverable']
root = 'https://app.doctorcre.com/api/v1/deal-deliverables/' + row['deal_id']
seen = []
class Opener:
    def open(self, request, timeout):
        seen.append(request.full_url)
        assert request.get_header('User-agent') == 'doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)'
        if len(seen) == 1:
            assert request.full_url == 'https://api.doctorcre.com/release'
            assert request.get_header('Cookie') is None
            key = 'release'
        elif len(seen) == 2:
            assert request.full_url == root
            key = 'list'
        else:
            assert request.full_url == root + '/' + row['id'] + '/download'
            key = 'revoked' if request.get_header('Cookie') == 'synthetic-revoked' else 'download'
        data = base64.b64decode(fixture[key]['base64']) if key == 'download' else json.dumps(fixture[key]).encode()
        response = io.BytesIO(data)
        response.headers = {}
        response.status = fixture[key]['status'] if key == 'revoked' else 200
        return response
proof.read_http = partial(proof.read_http, opener=Opener().open)
directory = sys.argv[3]
sys.argv = ['proof', '--receipt',sys.argv[1], '--sha','1'*40,'--active-cookie-file',directory+'/active-cookie','--revoked-cookie-file',directory+'/revoked-cookie']
assert proof.main() == 0
assert len(seen) == 4
`,
              `${directory}/receipt.json`,
              `${directory}/fixture.json`,
              directory,
              proofPath,
            ],
            { encoding: "utf8" },
          );
          assert.equal(
            transport.status,
            0,
            transport.stdout + transport.stderr,
          );
          for (const mutation of [
            () => {
              fixture.revoked.status = 200;
            },
            () => {
              fixture.revoked.status = 401;
              fixture.download.base64 = "";
            },
            () => {
              fixture.download.base64 = Buffer.from(data).toString("base64");
              receipt.share.deliverable.version = 99;
            },
          ]) {
            mutation();
            await writeFile(
              `${directory}/fixture.json`,
              JSON.stringify(fixture),
            );
            await writeFile(
              `${directory}/receipt.json`,
              JSON.stringify(receipt),
            );
            const fail = spawnSync("python3", command, { encoding: "utf8" });
            assert.equal(fail.status, 1, fail.stdout + fail.stderr);
          }
        } finally {
          await rm(directory, { recursive: true, force: true });
        }
      },
    );
  } finally {
    await db.close();
  }
});
