import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0577_work_request_card_admits_needs_joe.sql"), "utf8");

test("0577 widens ops.work_request_card to admit needs_joe without changing its signature", () => {
  assert.doesNotMatch(migration, /^(begin|commit);/m, "0339+ migrations run in the runner's single transaction");
  // Same signature, same create-or-replace -- not a drop-then-create, so the
  // function's existing grants are preserved across the redefinition.
  assert.match(migration,
    /create or replace function ops\.work_request_card\(p_work_request text, p_organization_tenant_id text\) RETURNS TABLE/);
  assert.doesNotMatch(migration, /drop function ops\.work_request_card/);
  // The state list admits needs_joe alongside the five existing states, and
  // invents no other state.
  assert.match(migration, /w\.state in \('captured','triaged','ready','needs_joe','declined','superseded'\)/);
  // A general (needs_joe) row never carries doctrine_section_id, so the
  // doctrine joins must be LEFT joins, not the original INNER joins -- an
  // INNER join here would make the state widening dead code.
  assert.match(migration, /left join public\.doctrine_section s on s\.id=w\.doctrine_section_id/);
  assert.match(migration, /left join public\.doctrine_document d on d\.id=s\.document_id/);
  assert.doesNotMatch(migration, /\n\s*join public\.doctrine_section s on s\.id=w\.doctrine_section_id/);
  assert.doesNotMatch(migration, /\n\s*join public\.doctrine_document d on d\.id=s\.document_id/);
  // source_current reads a real false rather than an ambiguous null when
  // there is no doctrine source to be current or stale against.
  assert.match(migration,
    /coalesce\(s\.status='active' and s\.current_revision_id=w\.doctrine_revision_id, false\)/);
  // Grants are re-issued identically, never widened to a new role.
  assert.match(migration, /grant execute on function ops\.work_request_card\(text,text\) to carr_reader,carr_writer;/);
  assert.doesNotMatch(migration, /to carr_reader,carr_writer,carr_jobs|to carr_authority|to public/);
});

test("0577 admits ONLY the general needs_joe row shape, never a general row in any other state", () => {
  // The exact single predicate the fix landed on: sourced/program rows pass
  // through their original condition (doctrine_section_id present, document
  // shared); a general row passes ONLY when it is needs_joe. No independent
  // OR anywhere in the WHERE clause admits a general row outside needs_joe --
  // an earlier draft's plain "organization_tenant_id is null or ..." /
  // "d.visibility is null or ..." widenings did exactly that (a general
  // captured row would have passed, and the handler would then hand it
  // "Review and triage", which review-and-triage refuses outright because it
  // is sourced-only) and the fixed predicate below is what closes it.
  assert.match(migration,
    /and \(\(w\.doctrine_section_id is not null and d\.visibility='shared' and w\.state<>'needs_joe'\)\s*\n\s*or \(w\.state='needs_joe' and w\.doctrine_section_id is null\)\);/);
  // The old two-predicate shape (independently OR-relaxed visibility, and a
  // state-name-only needs_joe guard) must be gone, not merely joined by a
  // third predicate -- otherwise the leak the review caught is still live.
  assert.doesNotMatch(migration, /\(d\.visibility is null or d\.visibility='shared'\)/);
  assert.doesNotMatch(migration, /\(w\.state<>'needs_joe' or w\.doctrine_section_id is null\)/);
});
