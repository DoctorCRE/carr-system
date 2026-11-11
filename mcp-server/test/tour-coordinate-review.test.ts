import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import pg from "pg";
import type { CoordinateReview } from "../src/tour-coordinate-review-contract.ts";
import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";

test("coordinate review extends authorized detail without granting a new callable capability", async () => {
  const fixture = await acquireDisposablePostgres({ prefix: "coordinate-review-", snapshot: true });
  const client = new pg.Client(fixture.connection);
  await client.connect();
  try {
    await client.query("begin");
    const installed = (
      await client.query(
        "select pg_get_functiondef('ops.read_tour_internal_detail(text,uuid,text)'::regprocedure) definition",
      )
    ).rows[0].definition;
    const old = installed.replace(
      "'coordinate_review',ops.tour_property_coordinate_review(s.organization_tenant_id,s.property_id,now()),",
      "",
    );
    assert.notEqual(old, installed);
    await client.query(old);
    await client.query("drop function ops.tour_property_coordinate_review(text,uuid,timestamptz)");
    await client.query("savepoint before_feature");
    await assert.rejects(
      client.query(
        "select ops.tour_property_coordinate_review('carr-internal','10000000-0000-4000-8000-000000000001',now())",
      ),
      /does not exist/,
    );
    await client.query("rollback to before_feature");
    await client.query(
      readFileSync(new URL("../../migrations/1180_tour_coordinate_review.sql", import.meta.url), "utf8"),
    );
    assert.equal(
      (
        await client.query(
          "select pg_get_functiondef('ops.read_tour_internal_detail(text,uuid,text)'::regprocedure) definition",
        )
      ).rows[0].definition,
      installed,
    );
    await client.query("commit");
    const result = await client.query(
      "select ops.tour_property_coordinate_review('carr-internal','10000000-0000-4000-8000-000000000001',now()) data",
    );
    assert.equal(result.rows[0].data.schema, "tour-coordinate-review.v1");
    assert.equal(result.rows[0].data.status, "unknown");
    assert.deepEqual(result.rows[0].data.candidates, []);
    const sql = readFileSync(new URL("./tour-delivery-data-plane-postgres.sql", import.meta.url), "utf8").replace(
      /^\\set ON_ERROR_STOP on\n/,
      "",
    );
    const setup = sql.split("do $map_promotion$")[0];
    assert.ok(setup);
    await client.query(setup);
    const read = async (tenant = "tour-delivery-proof", at = "now()") => {
      const response = await client.query(
        `select ops.tour_property_coordinate_review($1,'a1000000-0000-4000-8000-000000000001',${at}) data`,
        [tenant],
      );
      const value: CoordinateReview = response.rows[0].data;
      return value;
    };
    let review = await read();
    assert.equal(review.candidates.length, 1);
    assert.equal(review.property_name, "Current Medical Plaza");
    assert.equal(review.property_address, "100 Current Way");
    assert.equal(review.candidates[0]?.approval.status, "unverified");
    assert.deepEqual(review.candidates[0]?.position, { latitude: 30.421, longitude: -87.216 });
    assert.equal(review.candidates[0]?.source.stable_locator, "proof:search");
    assert.equal(review.candidates[0]?.rights.status, "usable");
    assert.equal((await read("other-tenant")).candidates.length, 0);
    assert.equal((await read("tour-delivery-proof", "now()-interval '1 day'")).candidates.length, 0);
    await client.query(`insert into ops.tour_property_coordinate_candidate
      (organization_tenant_id,property_id,coordinate_role,latitude,longitude,precision_class,source_evidence_id,rights_receipt_id,observed_at,review_state)
      values ('tour-delivery-proof','a1000000-0000-4000-8000-000000000001','parcel_centroid',30,-87,'parcel',
      'a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now(),'reviewed')`);
    review = await read();
    assert.equal(review.candidates.length, 2);
    assert.equal(
      review.candidates.find((row) => row.coordinate_role === "parcel_centroid")?.approval.status,
      "ineligible_role",
    );
    await client.query(`select ops.append_tour_entrance_verification_receipt(jsonb_build_object(
      'organization_tenant_id','tour-delivery-proof','property_id','a1000000-0000-4000-8000-000000000001',
      'coordinate_candidate_id','aa000000-0000-4000-8000-000000000001',
      'verifier_actor_id',(select id::text from public.actor where slug='joe' and active and kind='human'),
      'verified_at',now(),'evidence_reference','proof:entrance','native_navigation_proof','{"status":"passed"}'::jsonb,
      'receipt_digest','sha256:'||repeat('4',64)))`);
    review = await read();
    const entrance = review.candidates.find((row) => row.coordinate_role === "entrance");
    assert.equal(entrance?.approval.status, "verified_entrance");
    assert.equal(entrance?.approval.receipt?.evidence_reference, "proof:entrance");
    const actor = (await client.query("select id::text from public.actor where slug='joe' and active and kind='human'"))
      .rows[0].id;
    const detail = (
      await client.query(
        "select ops.read_tour_internal_detail('tour-delivery-proof','a2000000-0000-4000-8000-000000000001',$1) data",
        [actor],
      )
    ).rows[0].data;
    assert.deepEqual(detail.routes[0].stops[0].coordinate_review, review);
    assert.equal(detail.routes[0].navigation.status, "unavailable");
    await client.query(`insert into ops.tour_rights_receipt
      (organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status,supersedes_receipt_id)
      values ('tour-delivery-proof','search-proof','search-policy',2,'sha256:'||repeat('8',64),'https://example.invalid/terms',now(),'proof','replacement','["*"]','["canonical_fact"]',now(),'active','a5000000-0000-4000-8000-000000000001')`);
    review = await read();
    assert.equal(review.property_address, null);
    assert.ok(
      review.candidates.every(
        (row) => row.position === null && row.approval.status === "rights_unavailable" && row.approval.receipt === null,
      ),
    );
    await client.query("rollback");
    let recorded = "";
    client.on("notice", (notice) => {
      if (notice.message?.startsWith("COORDINATE_REVIEW_RECORD:"))
        recorded = notice.message.slice("COORDINATE_REVIEW_RECORD:".length);
    });
    await client.query(
      sql.replace(
        "raise notice 'TOUR_NAVIGATION_RECORD:%',nav;",
        "raise notice 'COORDINATE_REVIEW_RECORD:%',ops.read_tour_internal_detail('tour-delivery-proof','a2000000-0000-4000-8000-000000000001',v_actor_id);",
      ),
    );
    assert.ok(recorded);
    const proof = `import contextlib,importlib.util,io,json,sys
from types import SimpleNamespace
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('proof','tools/prove-tour-navigation.py')
p=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
d=json.load(sys.stdin)
r=d['routes'][0]
for broken in (False,True):
 if broken:
  r['stops'][0]['coordinate_review']['candidates'][0]['position']['latitude']+=0.000001
 release=dict(ok=True,env={'value':'production'},git_sha={'value':'a'*40})
 replies=[SimpleNamespace(status=200,body=json.dumps(value).encode()) for value in (release,{'data':d})]
 with patch.object(p,'read',side_effect=replies),patch.object(p.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(p.http.cookiejar.MozillaCookieJar,'load'),patch('sys.argv',['proof','--merge-sha','b'*40,'--tour-id',d['id'],'--route-id',r['id'],'--cookie-jar','/tmp/synthetic-proof-cookies','--coordinate-review']),contextlib.redirect_stdout(io.StringIO()):
  assert p.main()==(1 if broken else 0)
`;
    execFileSync("python3", ["-c", proof], {
      input: recorded,
      cwd: new URL("../../", import.meta.url),
      timeout: 30000,
    });
    for (const role of ["carr_reader", "carr_writer", "carr_jobs", "carr_authority"]) {
      const privilege = await client.query(
        "select has_function_privilege($1,'ops.tour_property_coordinate_review(text,uuid,timestamptz)','execute') allowed",
        [role],
      );
      assert.equal(privilege.rows[0].allowed, false);
    }
  } finally {
    await client.end();
    await fixture.close();
  }
});
