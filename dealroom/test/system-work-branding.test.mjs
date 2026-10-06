import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const html = await readFile(new URL("../system-work.html", import.meta.url), "utf8");

test("System Work carries DoctorCRE workspace branding in its metadata and header", () => {
  assert.match(html, /<meta name="description" content="DoctorCRE workspace system work/);
  assert.match(html, /<title>System Work · DoctorCRE Workspace<\/title>/);
  assert.match(html, /class="system-work-brand"><span>DoctorCRE<\/span><strong>System Work<\/strong><\/a>/);
});
