import { Validator } from "@cfworker/json-schema";
import { writeFile } from "node:fs/promises";
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { executeRegisteredTool } from "../src/tools.js";
import { restoreEventIdentity } from "./helpers/snapshot-schema.mjs";
import { postgresDatabase } from "./helpers/postgres-database.mjs";
import {
  DELIVERABLE_POLICY,
  DELIVERABLE_PREFIX,
} from "../src/deal-deliverables.js";
import { createDealDeliverablesHandler } from "../src/deal-deliverables-web.js";
const actor = { slug: "joe", human: true, id: randomUUID() };
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

test("document intake contract and lifecycle", async (t) => {
  const db = await postgresDatabase("document-intake-");
  const c = db.client;
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
      executeRegisteredTool(c, who, verb, { ...(!["read-deal-deliverable", "list-deal-documents", "list-deal-deliverables"].includes(verb) ? {idempotency_key: randomUUID()} : {}), ...args }),
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
    for (const name of ["event", "tool_call", "attachment"]) {
      const ddl = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`));
      assert.ok(ddl, name);
      await c.query(ddl[0]);
    }
    await restoreEventIdentity(c, schema);
    await c.query("alter table tool_call add primary key(idempotency_key)");
    await c.query(`create view v_ref_index as select 'deal'::text subject_type,id subject_id from deal`);
    await c.query(await readFile(new URL("../../migrations/1086_deal_documents.sql", import.meta.url), "utf8"));
    const reserve = (args = {}) => start({target_collection: "documents", ...args});
    const receipt = (id) => call("read-deal-deliverable", {deal_id:deal,deliverable_id:id});
    await t.test("reserve replay, photo upload, atomic attachment finalize and document readback", async () => {
      const photo = new Uint8Array([255,216,255,0,1,2,3]);
      const args = {idempotency_key:randomUUID(),filename:"Photo.jpg",mime:"image/jpeg",bytes:photo.length,sha256:createHash("sha256").update(photo).digest("hex")};
      const first = await reserve(args);
      const replay = await reserve(args);
      assert.equal(replay.deliverable.id, first.deliverable.id);
      assert.equal(replay.replayed,true);
      await reject(reserve({...args,filename:"Changed.jpg"}), "key_reuse");
      const id = first.deliverable.id;
      assert.equal((await receipt(id)).attachment,null);
      const uploaded=await put(id, photo);
      assert.equal(uploaded.status,201);
      const upload=await uploaded.json();
      const saved = await finalize(id);
      const contract = JSON.parse(await readFile(new URL("../../workspace/contracts/deal-document-intake.v1.json",import.meta.url),"utf8"));
      const validator = new Validator(contract.response_schemas.finalize.properties.attachment);
      assert.equal(validator.validate(saved.attachment).valid,true);
      assert.equal(validator.validate({...saved.attachment,bytes:26214401}).valid,false);
      assert.equal(saved.schema_version,"deal-document-intake.v1");
      assert.deepEqual(saved.attachment,{attachment_id:id,deal_id:deal,sha256:args.sha256,filename:args.filename,mime:args.mime,bytes:args.bytes});
      assert.deepEqual((await finalize(id)).attachment,saved.attachment);
      assert.deepEqual((await receipt(id)).attachment,saved.attachment);
      const list = await call("list-deal-documents",{deal_id:deal});
      assert.equal(list.schema_version,"deal-documents.v1");
      assert.deepEqual(list.documents.map(d=>[d.id,d.content_hash]),[[id,args.sha256]]);
      const fixture = {reserve:first,upload,finalize:saved,list};
      if (process.env.J76_FIXTURE_OUTPUT) await writeFile(process.env.J76_FIXTURE_OUTPUT,JSON.stringify(fixture,null,2)+"\n");
      const recorded=process.env.J76_FIXTURE_OUTPUT ? fixture : JSON.parse(await readFile(new URL("../../workspace/contracts/fixtures/deal-document-intake.v1.json",import.meta.url),"utf8"));
      for (const [name,schema] of Object.entries(contract.response_schemas)) {
        const responseValidator=new Validator(schema);
        assert.equal(responseValidator.validate(fixture[name]).valid,true, name);
        assert.equal(responseValidator.validate(recorded[name]).valid,true, name);
        assert.equal(responseValidator.validate({}).valid,false, name);
      }
      assert.equal(validator.validate(recorded.finalize.attachment).valid,true);
      assert.equal(recorded.finalize.attachment.attachment_id,recorded.list.documents[0].id);
      assert.equal(recorded.finalize.attachment.sha256,recorded.list.documents[0].content_hash);
      assert.deepEqual((await call("list-deal-deliverables",{deal_id:deal})).deliverables,[]);
      await reject(visibility(id,1),"deliverable_denied");
      await reject(call("abandon-deal-deliverable",{deal_id:deal,deliverable_id:id}),"deliverable_upload_conflict");
      assert.equal((await put(id,photo)).status,409);
    });
    await t.test("upload failure rolls back, hash mismatch, oversize, MIME and auth refusals", async () => {
      const pending = await reserve();
      const id = pending.deliverable.id;
      assert.equal((await put(id,Uint8Array.from(bytes, (b,i) => i===12 ? b^1 : b))).status,422);
      assert.equal((await put(id,new Uint8Array(bytes.length+1))).status,413);
      await reject(reserve({mime:"text/html"}),"unsupported_mime");
      await reject(reserve({bytes:DELIVERABLE_POLICY.maxBytes+1}),"upload_size_refused");
      await reject(reserve({deal_id:otherDeal}),"deliverable_denied");
      await reject(reserve({deal_id:randomUUID()}),"deliverable_denied");
      revoked=true; assert.equal((await put(id)).status,401); revoked=false;
      await reject(call("read-deal-deliverable",{deal_id:otherDeal,deliverable_id:id}),"deliverable_denied");
      await reject(finalize(id),"deliverable_not_verified");
      assert.equal((await put(id)).status,201);
      await finalize(id);
    });
    await t.test("finalize failure rolls back attachment and permits retry; receipt transport rechecks session", async () => {
      const id=(await reserve()).deliverable.id;
      assert.equal((await put(id)).status,201);
      await c.query(`create function fail_document_finalize() returns trigger language plpgsql as $$begin raise exception 'synthetic failure'; end$$;
        create trigger fail_document_finalize before update on ops.deal_deliverable for each row when (new.state='finalized') execute function fail_document_finalize()`);
      await assert.rejects(finalize(id), /synthetic failure/);
      assert.equal((await c.query("select count(*)::int n from attachment where id=$1",[id])).rows[0].n,0);
      assert.equal((await receipt(id)).deliverable.state,"verified");
      await c.query("drop trigger fail_document_finalize on ops.deal_deliverable");
      await finalize(id);
      const path=`${DELIVERABLE_PREFIX}/${deal}/${id}/receipt`;
      const visible=await handler(request(path),actor,async()=>true);
      assert.equal(visible.status,200);
      assert.equal((await visible.json()).attachment.attachment_id,id);
      assert.equal((await handler(request(path),actor,async()=>false)).status,401);
      assert.equal((await handler(request(path),{...actor,slug:"dell"},async()=>true)).status,403);
      const pending=(await reserve()).deliverable.id;
      const discarded=await handler(request(`${DELIVERABLE_PREFIX}/${deal}/${pending}/abandon`,"POST",JSON.stringify({idempotency_key:randomUUID()})),actor,async()=>true);
      assert.equal(discarded.status,200);
      assert.equal((await receipt(pending)).deliverable.state,"expired");
    });
    await t.test("abandon started and verified reservations, replay and expiry refuse finalization", async () => {
      for (const verified of [false,true]) {
        const id = (await reserve()).deliverable.id;
        if (verified) assert.equal((await put(id)).status,201);
        const discard = {deal_id:deal,deliverable_id:id,idempotency_key:randomUUID()};
        await call("abandon-deal-deliverable",discard);
        await call("abandon-deal-deliverable",discard);
        await reject(finalize(id),"deliverable_upload_expired");
        assert.equal((await put(id)).status,410);
      }
      for (const verified of [false,true]) {
        const id = (await reserve()).deliverable.id;
        if (verified) assert.equal((await put(id)).status,201);
        await c.query("update ops.deal_deliverable set expires_at=now()-interval '1 second' where id=$1",[id]);
        assert.equal((await receipt(id)).deliverable.state,"expired");
        await reject(finalize(id),"deliverable_upload_expired");
        assert.equal((await put(id)).status,410);
      }
    });
  } finally { await db.close(); }
});
