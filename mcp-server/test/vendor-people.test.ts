import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { vendorDirectoryPayload, vendorPeopleJoin, vendorPersonFields } from "../src/vendor-people.ts";

const digest = (value: string) => createHash("sha256").update(value).digest("hex");

test("vendor SQL retains sourced-office verification and stable person ordering byte for byte", () => {
  assert.equal(digest(vendorPeopleJoin), "08a4b920ac7f7424e0bb97e433524e3cf90efb118f391876408bddbfa4d2c079");
  assert.equal(digest(vendorPersonFields), "0ef17a8cfc616294e75e66c90903444921cfdfeeeb049dd12b29c3feb0462b71");
  assert.equal(
    digest(vendorDirectoryPayload("vendor")),
    "63418bd0a3a95b1c76f1d780f3e76906b0e3e9728ce85b0498590a0096be13ff",
  );
});

test("directory projection interpolates the caller's alias at both row references", () => {
  const payload = vendorDirectoryPayload("directory");
  assert.ok(payload.startsWith("directory.row_payload::jsonb"));
  assert.ok(payload.includes("where a.id=directory.id and nullif(btrim(name),'') is not null"));
  assert.ok(payload.includes(vendorPeopleJoin));
  assert.ok(payload.includes(vendorPersonFields));
});
