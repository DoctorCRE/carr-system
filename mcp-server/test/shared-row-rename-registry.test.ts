// Rule 8cddc6ad: a shared row may be renamed only through a surface that first
// counts/re-points its attachments.  There is no such public writer today.
// Keep this deliberately static: adding a direct shared-row rename now breaks
// CI until its attachment-count gate and a corresponding registry case exist.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");

const SHARED_ROW_RENAME_SQL = [
  { label: "party name/org parent", re: /update\s+party\s+set[\s\S]{0,400}?\b(?:name|org_id)\s*=/gi },
  { label: "vendor category slug", re: /update\s+vendor_category\s+set[\s\S]{0,400}?\bslug\s*=/gi },
  // Reference/category tables are shared vocabulary; do not make a rename
  // writer by accident while adding one-off maintenance verbs.
  {
    label: "shared reference/category label",
    re: /update\s+\w*(?:ref|category)\w*\s+set[\s\S]{0,400}?\b(?:name|label|slug)\s*=/gi,
  },
];

test("shared-row rename registry remains empty until an attachment-count-gated writer exists", () => {
  const found = SHARED_ROW_RENAME_SQL.flatMap(({ label, re }) =>
    [...source.matchAll(re)].map((m) => ({ label, sql: m[0].replace(/\s+/g, " ").slice(0, 180) })),
  );
  assert.deepEqual(
    found,
    [],
    "A new shared-row rename surface needs an attachment-count gate plus an explicit registry entry and handler test (8cddc6ad).",
  );
});
