// doctrine.js — the doctrine store's verb surface + gate framework (P2 of the
// doctrine-store build; design/doctrine-store-build-2026-08-07.md, council
// decision 82a2fb62 amended by 20dfdfcc; schema: migrations/0075).
//
// SHAPE. A factory, not a module of free verbs: tools.js calls doctrineTools()
// with its own envelope machinery so this file runs the SAME idempotency,
// event-log and error contracts as every other verb, with no circular import.
//
// THE GATE FRAMEWORK. Validation is a REGISTRY, never verb code: each check is
// a row in doctrine_gate_check (severity, applies_to, config) plus a function
// in GATE_IMPLS below. A new gate ships as a function + an INSERT — no verb
// rewrite (council verdict, unanimous). Every run persists to doctrine_gate_run
// + findings, dry or real; any failed `block` finding aborts the transaction
// with the findings in the error payload, precise enough to self-repair.

import { organizationTenantForActor, personalScopeForActor } from "./identity.js";
import { searchDoctrineSituations } from "./situation-retrieval.js";
import { CORE_RULE_IDS, CORE_RULE_COUNT, CORE_RULE_TRIAGE_SOURCE } from "./core-rule-ids.js";
import { ruleBootPage } from "./rule-boot.js";

const CORE_RULE_ID_SET = new Set(CORE_RULE_IDS);

/** Read the count banner emitted by the generated personal/shared rule renders. */
export function generatedRuleCount(rendered) {
  const match = String(rendered || "").match(/\b(\d+) active rule\(s\)/);
  if (!match) throw new Error("generated rule render has no active-rule count");
  return Number(match[1]);
}
//
// P2 HONESTY NOTES, each with its named completion point:
//   * body model is {text} — plain prose sections. Structured per-class JSON
//     schemas (index entries as edges, dossier fields) land with P4's forced
//     batch, where the first structured class (index) migrates.
//   * banned_phrases seeds from the gate row's config; the code fallback list
//     is the writing-rules hard core (em-dash + the flagged-vocab shortlist).
//     The full linter stays lint-gate.py; this gate is the DB-side floor.
//   * claims exist as a table + gate since 0075; the claim/renew/release verbs
//     are P3 scope. Until then no claim rows exist, so the gate passes clean.
//   * search is the FTS floor (websearch_to_tsquery over current revisions).
//     Ranking polish and telemetry are P3 acceptance items.


export function doctrineTools({ withEnvelope, writeEvent, ToolError }) {

  // ---------------------------------------------------------------- helpers

  async function actorId(c, actor) {
    // The READER path hands verbs an actor without a row id (mcp.js resolves
    // ids only inside writer transactions). Personal-visibility filters need
    // the id, so read verbs resolve it here — one select, cached on the actor
    // object for the call. Found live 2026-08-08: without this, a partner's
    // own personal docs were invisible to him on every read verb.
    if (actor.id) return actor.id;
    const r = await c.query(`select id from actor where slug=$1`, [actor.slug]);
    actor.id = r.rows.length ? r.rows[0].id : null;
    return actor.id;
  }

  async function resolveDoc(c, ref) {
    // by id, live slug, or alias — aliases keep old links resolving (0075)
    let r = await c.query(
      `select * from doctrine_document where id::text = $1 or slug = $1`, [ref]);
    if (!r.rows.length) {
      r = await c.query(
        `select d.* from doctrine_slug_alias a
           join doctrine_document d on d.id = a.document_id
          where a.alias_slug = $1`, [ref]);
    }
    if (!r.rows.length)
      throw new ToolError({ error: "doctrine_doc_not_found", ref,
        hint: "pass a document id or slug; see doctrine-index for the catalog" });
    return r.rows[0];
  }

  async function lockSection(c, sectionId) {
    const r = await c.query(
      `select s.*, d.content_class, d.visibility, d.owner_actor_id, d.slug as doc_slug
         from doctrine_section s join doctrine_document d on d.id = s.document_id
        where s.id = $1 for update of s`, [sectionId]);
    if (!r.rows.length)
      throw new ToolError({ error: "doctrine_section_not_found", section_id: sectionId });
    return r.rows[0];
  }

  function versionConflict(c, section) {
    // The machine-readable conflict contract (council P2 acceptance): who wrote
    // last, when, from which session — never a bare "conflict" string.
    return c.query(
      `select r.version, r.created_at, r.session_key, a.slug as actor
         from doctrine_revision r join actor a on a.id = r.actor_id
        where r.section_id = $1 order by r.version desc limit 1`, [section.id]
    ).then(last => new ToolError({
      error: "version_conflict",
      section_id: section.id, section_key: section.section_key,
      current_version: Number(section.current_version),
      last_writer: last.rows[0] || null,
      resolution: "re-read the section (read-doctrine), re-plan against the current body, retry with base_version=current_version. Never overwrite blind.",
    }));
  }

  async function bumpGeneration(c) {
    const g = await c.query(
      `update doctrine_meta set generation = generation + 1, updated_at = now()
        where id = 1 returning generation`);
    return Number(g.rows[0].generation);
  }

  async function rebuildSnapshot(c, docId, generation) {
    await c.query(
      `insert into doctrine_snapshot (document_id, generation, snapshot_json, content_hash, built_at)
       select d.id, $2,
              jsonb_build_object(
                'document', jsonb_build_object('id', d.id, 'slug', d.slug, 'title', d.title,
                  'content_class', d.content_class, 'visibility', d.visibility),
                'sections', coalesce((
                  select jsonb_agg(jsonb_build_object(
                           'section_id', s.id, 'section_key', s.section_key, 'title', s.title,
                           'ordinal', s.ordinal, 'version', s.current_version,
                           'status', s.status, 'review_after', s.review_after,
                           'body', r.body, 'content_hash', r.content_hash)
                         order by s.ordinal)
                    from doctrine_section s
                    left join doctrine_revision r on r.id = s.current_revision_id
                   where s.document_id = d.id and s.status = 'active'), '[]'::jsonb),
                'edges_out', coalesce((
                  select jsonb_agg(jsonb_build_object('edge_type', e.edge_type,
                           'source_section_id', e.source_section_id,
                           'target_section_id', e.target_section_id, 'scope', e.scope))
                    from doctrine_edge e
                    join doctrine_section s on s.id = e.source_section_id
                   where s.document_id = d.id and e.retired_by_revision_id is null), '[]'::jsonb)),
              md5((select coalesce(string_agg(s.body_hash, '' order by s.ordinal), '')
                     from doctrine_section s where s.document_id = d.id)),
              now()
         from doctrine_document d where d.id = $1
       on conflict (document_id) do update
         set generation = excluded.generation, snapshot_json = excluded.snapshot_json,
             content_hash = excluded.content_hash, built_at = excluded.built_at`,
      [docId, generation]);
  }

  // ------------------------------------------------------------ gate runner

  // The writing-rules hard core as a code FALLBACK; the gate row's config
  // ({"phrases": [...]}) overrides it without a deploy. Em-dash is the first
  // citizen of the ban list by standing rule.
  const BANNED_FALLBACK = ["—", "delve", "tapestry", "furthermore,", "in conclusion,"];

  const GATE_IMPLS = {
    "gates.base_version_match": async () => [],   // enforced structurally in the verb; row exists for audit completeness
    "gates.body_schema": async (c, ctx) => {
      if (ctx.op !== "write") return [];
      const t = ctx.body && ctx.body.text;
      if (typeof t !== "string" || !t.trim())
        return [{ message: "body.text must be a non-empty string (P2 body model)", path: "body.text" }];
      return [];
    },
    "gates.banned_phrases": async (c, ctx, cfg) => {
      if (ctx.op !== "write") return [];
      // exempt_slugs (gate config): the documents that DEFINE the bans quote
      // them as examples — writing-rules cannot be blocked by itself. P3 add.
      if (cfg && (cfg.exempt_slugs || []).includes(ctx.doc_slug)) return [];
      const phrases = (cfg && cfg.phrases) || BANNED_FALLBACK;
      const text = ((ctx.body && ctx.body.text) || "").toLowerCase();
      return phrases.filter(p => text.includes(p.toLowerCase()))
        .map(p => ({ message: `banned phrase present: ${JSON.stringify(p)} (writing-rules hard ban)`,
                     path: "body.text" }));
    },
    "gates.slug_unique": async (c, ctx) => {
      if (ctx.op !== "create") return [];
      const dup = await c.query(
        `select 1 from doctrine_document where slug=$1
         union all select 1 from doctrine_slug_alias where alias_slug=$1`, [ctx.slug]);
      return dup.rows.length ? [{ message: `slug '${ctx.slug}' already taken (document or alias)`,
                                  path: "slug" }] : [];
    },
    "gates.personal_owner_required": async (c, ctx) => {
      if (ctx.visibility === "personal" && !ctx.owner_actor_id)
        return [{ message: "visibility=personal requires owner_actor_id", path: "visibility" }];
      return [];
    },
    "gates.claim_holder_or_free": async (c, ctx) => {
      if (!ctx.section_id) return [];
      const cl = await c.query(
        `select holder_actor_id, holder_session_key, expires_at from doctrine_claim
          where section_id = $1 and expires_at > now()`, [ctx.section_id]);
      if (cl.rows.length && cl.rows[0].holder_actor_id !== ctx.actor.id)
        return [{ message: `section claimed by another writer until ${cl.rows[0].expires_at.toISOString()} — wait for expiry or pick other work`, path: "claim" }];
      return [];
    },
    "gates.no_md_escape": async (c, ctx) => {
      if (ctx.op !== "write") return [];
      const text = (ctx.body && ctx.body.text) || "";
      // Narrow on purpose: an INSTRUCTION to write vault markdown, not a mere
      // mention of a filename. Over-firing burns a gate's credibility.
      if (/\b(write|create|save|append)\b[^.\n]{0,80}CARR AI\/[^\s]*\.md/i.test(text))
        return [{ message: "body instructs writing vault .md — the store IS the destination (rule 14181e60)", path: "body.text" }];
      return [];
    },
    "gates.target_exists": async (c, ctx) => {
      const out = [];
      for (const l of ctx.links || []) {
        const table = { doctrine_document: "doctrine_document", doctrine_section: "doctrine_section",
                        party: "party", deal: "deal", decision: "event", rule: "rule",
                        loop: "loop_item", capture: "source_capture" }[l.target_kind];
        if (!table) { out.push({ message: `unknown link target_kind '${l.target_kind}'`, path: "links" }); continue; }
        const r = await c.query(`select 1 from ${table} where id = $1`, [l.target_id]);
        if (!r.rows.length) out.push({ message: `link target ${l.target_kind}/${l.target_id} does not exist`, path: "links" });
      }
      for (const e of ctx.edges || []) {
        const r = await c.query(`select 1 from doctrine_section where id = $1 and status='active'`, [e.target_section_id]);
        if (!r.rows.length) out.push({ message: `edge target section ${e.target_section_id} not found or retired`, path: "edges" });
      }
      return out;
    },
    "gates.edge_class_allowed": async (c, ctx) => {
      const out = [];
      for (const e of ctx.edges || []) {
        const r = await c.query(`select 1 from doctrine_edge_type where edge_type = $1`, [e.edge_type]);
        if (!r.rows.length) out.push({ message: `unknown edge_type '${e.edge_type}'`, path: "edges" });
      }
      return out;
    },
    "gates.edge_acyclic": async (c, ctx) => {
      // Proposed acyclic edges must not close a cycle over the LIVE edge set.
      const out = [];
      for (const e of ctx.edges || []) {
        const et = await c.query(`select acyclic from doctrine_edge_type where edge_type=$1`, [e.edge_type]);
        if (!et.rows.length || !et.rows[0].acyclic) continue;
        const cyc = await c.query(
          `with recursive walk(id) as (
             select $2::uuid
             union
             select de.target_section_id from doctrine_edge de
               join walk w on w.id = de.source_section_id
              where de.edge_type = $3 and de.retired_by_revision_id is null)
           select 1 from walk where id = $1::uuid`,
          [ctx.section_id, e.target_section_id, e.edge_type]);
        if (cyc.rows.length)
          out.push({ message: `${e.edge_type} edge to ${e.target_section_id} closes a cycle`, path: "edges" });
      }
      return out;
    },
    "gates.unresolved_conflict": async (c, ctx) => {
      const out = [];
      for (const e of ctx.edges || []) {
        if (e.edge_type !== "CONFLICTS_WITH") continue;
        const resolver = (ctx.edges || []).some(o =>
          ["OVERRIDES", "SUPERSEDES"].includes(o.edge_type) &&
          o.target_section_id === e.target_section_id);
        const existing = await c.query(
          `select 1 from doctrine_edge
            where edge_type in ('OVERRIDES','SUPERSEDES') and retired_by_revision_id is null
              and ((source_section_id=$1 and target_section_id=$2)
                or (source_section_id=$2 and target_section_id=$1))`,
          [ctx.section_id, e.target_section_id]);
        if (!resolver && !existing.rows.length)
          out.push({ message: `CONFLICTS_WITH ${e.target_section_id} has no resolving OVERRIDES/SUPERSEDES edge — a conflict without a winner blocks commit`, path: "edges" });
      }
      return out;
    },
  };

  async function runGates(c, actor, op, ctx, { dryRun = false, changeSetId = null } = {}) {
    const checks = (await c.query(
      `select check_key, severity, applies_to, impl_key, config
         from doctrine_gate_check where enabled order by check_key`)).rows
      .filter(ch => {
        const ops = (ch.applies_to && ch.applies_to.ops) || null;
        if (ops && !ops.includes(op)) return false;
        const classes = ch.applies_to && ch.applies_to.content_classes;
        if (classes && ctx.content_class && !classes.includes(ctx.content_class)) return false;
        return true;
      });
    const findings = [];
    for (const ch of checks) {
      const impl = GATE_IMPLS[ch.impl_key];
      if (!impl) {                       // unknown impl = the registry drifted from the deploy
        findings.push({ check_key: ch.check_key, severity: "block", passed: false,
          message: `gate impl '${ch.impl_key}' missing from deploy — registry/deploy drift`, path: "" });
        continue;
      }
      const fails = await impl(c, { ...ctx, op, actor }, ch.config);
      for (const f of fails)
        findings.push({ check_key: ch.check_key, severity: ch.severity, passed: false, ...f });
      if (!fails.length)
        findings.push({ check_key: ch.check_key, severity: ch.severity, passed: true,
          message: "ok", path: "" });
    }
    const failed = findings.filter(f => !f.passed);
    const blocked = failed.some(f => f.severity === "block");
    const run = await c.query(
      `insert into doctrine_gate_run (change_set_id, dry_run, actor_id, finished_at, result, report)
       values ($1,$2,$3,now(),$4,$5) returning id`,
      [changeSetId, dryRun, actor.id, blocked ? "fail" : "pass", JSON.stringify(findings)]);
    for (const f of findings.filter(x => !x.passed))
      await c.query(
        `insert into doctrine_gate_finding (run_id, check_key, severity, passed, message, path)
         values ($1,$2,$3,$4,$5,$6) on conflict do nothing`,
        [run.rows[0].id, f.check_key, f.severity, f.passed, f.message, f.path || ""]);
    if (blocked && !dryRun)
      throw new ToolError({ error: "gate_blocked", gate_run_id: run.rows[0].id,
        findings: failed,
        hint: "each finding names its fix; repair and retry with a NEW idempotency key" });
    return { gate_run_id: run.rows[0].id, result: blocked ? "fail" : "pass", findings };
  }

  async function sha256hex(text) {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
  }

  function validateChangeDoctrineItems(items) {
    if (!Array.isArray(items))
      throw new ToolError({ error: "change_set_items_invalid",
        hint: "items must be an array of section changes" });

    const malformed = [];
    for (const [index, item] of items.entries()) {
      const fields = [];
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        malformed.push({ item_index: index, fields: ["item"] });
        continue;
      }
      if (typeof item.section_key !== "string" || !item.section_key.trim())
        fields.push("section_key");
      if (typeof item.body_text !== "string" || !item.body_text.trim())
        fields.push("body_text");
      if (!Number.isInteger(item.base_version) || item.base_version < 0)
        fields.push("base_version");
      if (item.title !== undefined && item.title !== null && typeof item.title !== "string")
        fields.push("title");
      if (item.ordinal !== undefined && item.ordinal !== null && !Number.isInteger(item.ordinal))
        fields.push("ordinal");
      if (fields.length) malformed.push({ item_index: index, fields });
    }
    if (malformed.length)
      throw new ToolError({ error: "change_set_item_invalid", malformed,
        hint: "each item requires a non-empty section_key, body_text, and non-negative integer base_version" });
    return items;
  }

  // ------------------------------------------------------------------ verbs

  return {

    "new-doctrine-doc": {
      write: true,
      description: "Create a doctrine document in the store — the database-first home for prose that used to live as vault markdown (rule 14181e60). content_class: playbook | sop | index | reference | dossier_narrative | distillation ('rule' is reserved until the P7 rule-store ruling). Sections are added with write-doctrine-section.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        slug: { type: "string", description: "kebab-case, permanent-ish; renames keep an alias" },
        title: { type: "string" },
        content_class: { type: "string" },
        visibility: { type: "string", description: "shared (default) | personal" },
        review_policy: { type: "string", description: "policy name; defaults by content_class" } },
        required: ["idempotency_key", "slug", "title", "content_class"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "new-doctrine-doc", args, async () => {
        if (args.content_class === "rule")
          throw new ToolError({ error: "content_class_reserved",
            hint: "'rule' waits for the P7 rule-store study (decision 82a2fb62)" });
        await runGates(c, actor, "create", { slug: args.slug,
          visibility: args.visibility || "shared",
          owner_actor_id: args.visibility === "personal" ? actor.id : null });
        const pol = await c.query(
          `select id from doctrine_review_policy
            where ($1::text is not null and name = $1)
               or ($1::text is null and $2 = any(content_classes)) limit 1`,
          [args.review_policy || null, args.content_class]);
        const r = await c.query(
          `insert into doctrine_document (slug, title, content_class, visibility,
             owner_actor_id, review_policy_id, created_by)
           values ($1,$2,$3,$4,$5,$6,$7) returning id`,
          [args.slug, args.title, args.content_class, args.visibility || "shared",
           args.visibility === "personal" ? actor.id : null,
           pol.rows.length ? pol.rows[0].id : null, actor.id]);
        await writeEvent(c, actor, "new-doctrine-doc", "doctrine_document", r.rows[0].id,
          { new: { slug: args.slug, content_class: args.content_class },
            idempotency_key: args.idempotency_key });
        return { ok: true, document_id: r.rows[0].id, slug: args.slug };
      }),
    },

    "write-doctrine-section": {
      write: true,
      description: "Write ONE section of a doctrine document — create it (base_version 0) or replace its body (base_version = current_version from a fresh read). Validation gates run as hard preconditions (body shape, banned phrases, claims); a block finding aborts with the findings, each naming its fix. Every write is an append-only revision; the section's history is never lost. Bumps the store generation and rebuilds the document snapshot.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        document: { type: "string", description: "document id or slug" },
        section_key: { type: "string", description: "stable key within the document" },
        title: { type: "string" },
        body_text: { type: "string", description: "the section's prose (P2 body model)" },
        base_version: { type: "integer", description: "0 to create; else current_version from a fresh read" },
        ordinal: { type: "integer" },
        commit_message: { type: "string" } },
        required: ["idempotency_key", "document", "section_key", "body_text", "base_version"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "write-doctrine-section", args, async () => {
        const doc = await resolveDoc(c, args.document);
        let sec = (await c.query(
          `select * from doctrine_section where document_id=$1 and section_key=$2 for update`,
          [doc.id, args.section_key])).rows[0];
        if (sec && Number(sec.current_version) !== args.base_version)
          throw await versionConflict(c, sec);
        if (!sec && args.base_version !== 0)
          throw new ToolError({ error: "section_not_found_base_nonzero",
            hint: "creating a section takes base_version 0" });
        const body = { text: args.body_text };
        await runGates(c, actor, "write", { section_id: sec ? sec.id : null, body,
          content_class: doc.content_class, visibility: doc.visibility,
          owner_actor_id: doc.owner_actor_id, doc_slug: doc.slug });
        if (doc.visibility === "personal" && doc.owner_actor_id !== actor.id)
          throw new ToolError({ error: "personal_doc_not_owner" });
        if (!sec) {
          const ord = args.ordinal ?? (await c.query(
            `select coalesce(max(ordinal),0)+10 as o from doctrine_section where document_id=$1`,
            [doc.id])).rows[0].o;
          sec = (await c.query(
            `insert into doctrine_section (document_id, section_key, title, ordinal)
             values ($1,$2,$3,$4) returning *`,
            [doc.id, args.section_key, args.title || null, ord])).rows[0];
        } else if (args.title) {
          await c.query(`update doctrine_section set title=$2 where id=$1`, [sec.id, args.title]);
        }
        const cs = await c.query(
          `insert into doctrine_change_set (actor_id, session_key, idempotency_key, state, committed_at)
           values ($1,$2,$3,'committed',now()) returning id`,
          [actor.id, actor.client_id || null, args.idempotency_key]);
        const hash = await sha256hex(args.body_text);
        const newVersion = Number(sec.current_version) + 1;
        const rev = await c.query(
          `insert into doctrine_revision (section_id, version, parent_revision_id, change_set_id,
             actor_id, session_key, body, plain_text, content_hash, commit_message)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) returning id`,
          [sec.id, newVersion, sec.current_revision_id, cs.rows[0].id, actor.id,
           actor.client_id || null, JSON.stringify(body), args.body_text, hash,
           args.commit_message || null]);
        await c.query(
          `update doctrine_section
              set current_revision_id=$2, current_version=$3, body_hash=$4, updated_at=now(),
                  review_after = case when $5::int is not null then now() + ($5::int || ' days')::interval else review_after end
            where id=$1`,
          [sec.id, rev.rows[0].id, newVersion, hash,
           (await c.query(`select p.max_age_days from doctrine_document d
                             left join doctrine_review_policy p on p.id = d.review_policy_id
                            where d.id=$1`, [doc.id])).rows[0].max_age_days]);
        const generation = await bumpGeneration(c);
        await rebuildSnapshot(c, doc.id, generation);
        await writeEvent(c, actor, "write-doctrine-section", "doctrine_section", sec.id,
          { new: { doc: doc.slug, section_key: args.section_key, version: newVersion },
            idempotency_key: args.idempotency_key });
        return { ok: true, section_id: sec.id, version: newVersion,
                 revision_id: rev.rows[0].id, generation };
      }),
    },

    "set-doctrine-refs": {
      write: true,
      description: "Replace a section's outbound links and typed edges in one atomic act. Links are citations (may target parties, deals, decisions, loops, captures); edges carry doctrine semantics (OVERRIDES / SUPERSEDES / EXCEPTION_TO / DEPENDS_ON / APPLIES_TO / CONFLICTS_WITH). Gates enforce: targets exist, edge types legal, acyclic types stay acyclic, and a CONFLICTS_WITH without a resolving edge blocks — a conflict without a winner never commits.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        section_id: { type: "string" },
        base_version: { type: "integer" },
        links: { type: "array", items: { type: "object", properties: {
          target_kind: { type: "string" }, target_id: { type: "string" },
          role: { type: "string" } }, required: ["target_kind", "target_id"] } },
        edges: { type: "array", items: { type: "object", properties: {
          edge_type: { type: "string" }, target_section_id: { type: "string" },
          scope: { type: "object" } }, required: ["edge_type", "target_section_id"] } } },
        required: ["idempotency_key", "section_id", "base_version"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-doctrine-refs", args, async () => {
        const sec = await lockSection(c, args.section_id);
        if (Number(sec.current_version) !== args.base_version)
          throw await versionConflict(c, sec);
        await runGates(c, actor, "refs_set", { section_id: sec.id,
          content_class: sec.content_class,
          links: args.links || [], edges: args.edges || [] });
        await c.query(`delete from doctrine_link where source_section_id=$1`, [sec.id]);
        for (const l of args.links || [])
          await c.query(
            `insert into doctrine_link (source_section_id, target_kind, target_id, role)
             values ($1,$2,$3,$4)`, [sec.id, l.target_kind, l.target_id, l.role || "citation"]);
        await c.query(
          `update doctrine_edge set retired_by_revision_id = $2
            where source_section_id = $1 and retired_by_revision_id is null`,
          [sec.id, sec.current_revision_id]);
        for (const e of args.edges || [])
          await c.query(
            `insert into doctrine_edge (source_section_id, target_section_id, edge_type, scope,
               introduced_by_revision_id)
             values ($1,$2,$3,$4,$5)`,
            [sec.id, e.target_section_id, e.edge_type, JSON.stringify(e.scope || {}),
             sec.current_revision_id]);
        const generation = await bumpGeneration(c);
        await rebuildSnapshot(c, sec.document_id, generation);
        await writeEvent(c, actor, "set-doctrine-refs", "doctrine_section", sec.id,
          { new: { links: (args.links || []).length, edges: (args.edges || []).length },
            idempotency_key: args.idempotency_key });
        return { ok: true, section_id: sec.id,
                 links: (args.links || []).length, edges: (args.edges || []).length, generation };
      }),
    },

    "retire-doctrine-section": {
      write: true,
      description: "Retire a section (status='retired'): it leaves snapshots and search but its history stays queryable forever. Requires base_version. DEPENDS_ON gates elsewhere treat a retired target as missing, so retiring a depended-on section will surface in the next write against the dependent.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        section_id: { type: "string" },
        base_version: { type: "integer" },
        reason: { type: "string" } },
        required: ["idempotency_key", "section_id", "base_version", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "retire-doctrine-section", args, async () => {
        const sec = await lockSection(c, args.section_id);
        if (Number(sec.current_version) !== args.base_version)
          throw await versionConflict(c, sec);
        await runGates(c, actor, "retire", { section_id: sec.id, content_class: sec.content_class });
        await c.query(
          `update doctrine_section set status='retired', updated_at=now(),
                  current_version = current_version + 1 where id=$1`, [sec.id]);
        const generation = await bumpGeneration(c);
        await rebuildSnapshot(c, sec.document_id, generation);
        await writeEvent(c, actor, "retire-doctrine-section", "doctrine_section", sec.id,
          { new: { reason: args.reason }, idempotency_key: args.idempotency_key });
        return { ok: true, section_id: sec.id, status: "retired", generation };
      }),
    },

    "read-doctrine": {
      description: "Read a whole doctrine document — one round trip, snapshot-served. Pass if_generation_match to short-circuit when nothing changed (returns not_modified). This is THE doctrine read path on every surface; there is no file to read (decision 20dfdfcc).",
      inputSchema: { type: "object", properties: {
        document: { type: "string", description: "id, slug, or old slug (aliases resolve)" },
        if_generation_match: { type: "integer" } },
        required: ["document"] },
      handler: async (c, actor, args) => {
        await actorId(c, actor);
        const doc = await resolveDoc(c, args.document);
        // Personal doctrine is readable by both partners (decision logged
        // 2026-09-24): "personal" scopes where a rule APPLIES, not who may
        // see it. Writes stay owner-only; search-doctrine and
        // resolve-doctrine-rules still apply only the caller's own set.
        const snap = await c.query(
          `select generation, snapshot_json, content_hash, built_at
             from doctrine_snapshot where document_id = $1`, [doc.id]);
        const gen = (await c.query(`select generation from doctrine_meta where id=1`)).rows[0];
        if (args.if_generation_match !== undefined && snap.rows.length
            && Number(snap.rows[0].generation) === args.if_generation_match)
          return { ok: true, not_modified: true, generation: Number(snap.rows[0].generation) };
        if (snap.rows.length)
          return { ok: true, generation: Number(snap.rows[0].generation),
                   store_generation: Number(gen.generation),
                   built_at: snap.rows[0].built_at, ...snap.rows[0].snapshot_json };
        // no snapshot yet (doc with no sections): serve live
        return { ok: true, generation: Number(gen.generation),
                 document: { id: doc.id, slug: doc.slug, title: doc.title,
                             content_class: doc.content_class, visibility: doc.visibility },
                 sections: [], edges_out: [] };
      },
    },

    "map-architecture": {
      description: "Mandatory first read for any CARR map, GIS, route, day-trip, or Tour request. Returns the two current live doctrine sections that govern map construction plus the machine-contract identity and the non-bypass workflow. Call this before recommending a stack, researching layers, designing, editing, or publishing a map. Fails visibly if either source is missing or retired.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => {
        const required = [
          ["maps-and-demographics", "ai-built-interactive-tour-maps-source-rendering-routing-and-promotion-gate"],
          ["carr-workspace-bduf", "s13-ipad-application-and-tour-mode"],
        ];
        const rows = await c.query(
          `select d.slug as document_slug, s.section_key, s.title,
                  s.current_version as version, r.body
             from doctrine_section s
             join doctrine_document d on d.id = s.document_id
             join doctrine_revision r on r.id = s.current_revision_id
            where s.status = 'active'
              and ((d.slug = $1 and s.section_key = $2)
                or (d.slug = $3 and s.section_key = $4))`,
          required.flat());
        const byKey = new Map(rows.rows.map(row =>
          [`${row.document_slug}/${row.section_key}`, row]));
        const missing = required.map(([doc, section]) => `${doc}/${section}`)
          .filter(key => {
            const row = byKey.get(key);
            return !row || !row.body || typeof row.body.text !== "string" || !row.body.text.trim();
          });
        if (missing.length)
          throw new ToolError({ error: "map_architecture_unavailable", missing,
            hint: "The governed map method is fail-closed. Restore or activate every required doctrine section before map work continues." });
        const generation = (await c.query(
          `select generation from doctrine_meta where id=1`)).rows[0];
        return {
          ok: true,
          architecture: "carr-map-tour-v1",
          doctrine_generation: Number(generation.generation),
          contract: {
            id: "carr-workspace-market-map-route-planning",
            version: "1.2.0",
            path: "workspace/contracts/market-map-route-planning.v1.json",
            production_status: "approved_architecture_not_implemented_in_production",
          },
          method_ids: [
            "recursive_source_intake",
            "typed_domain_queries",
            "spatial_authoring_workbench",
            "deterministic_component_registry",
            "portable_geospatial_interchange",
            "entrance_level_coordinate_verification",
            "route_label_identity_separation",
            "search_and_tour_modes",
            "map_event_contract",
            "provider_rights_receipt",
            "human_promotion_receipt",
          ],
          required_workflow: [
            "Start with the business question. Recursively read direct, quoted, threaded, linked, video, repository, documentation, and dataset sources; label direct evidence, linked artifacts, public mirrors, inference, and inaccessible branches.",
            "Use typed domain queries for lookup, text, bbox, radius, polygon, batch, and time-series retrieval; perform analysis with explicit freshness, provenance, precision, estimate/allocation, pagination, and rate-limit caveats.",
            "Build one canonical, reviewed dataset from CARR records and separately reviewed GIS or OSINT enrichment; never let the map, geocoder, route provider, or model become source truth.",
            "Use the bounded spatial authoring workbench for draft import, normalize, join, filter, style, compare, validate, export, and disposable prototypes. Promote only deterministic map components and typed functions that are tested and versioned.",
            "Use GeoJSON for small web working sets, GeoPackage for editable GIS bundles, GeoParquet plus DuckDB for analysis, and PMTiles for large read-only layers while preserving stable IDs and provenance.",
            "Approve an entrance, driveway, or parking-access coordinate before navigation. Parcel centroids, building centroids, geocoder candidates, and apparent parking-lot pins visibly downgrade or exclude a stop.",
            "Keep property identity separate from mutable route_sequence and route_label. Marker, list, route, story section, and offline itinerary derive from one route version.",
            "Use Search Mode for typed spatial discovery and Tour Mode for deterministic camera, card, marker, route, and optional overlay choreography. Update state through typed events without remounting the map.",
            "Use provider-backed geocoding and routing with a current rights receipt; preserve locked appointments, dwell, buffers, start/end points, and visible exclusions.",
            "Hand exact approved coordinates to native Google Maps or Apple Maps navigation and require a human promotion receipt with mobile, native-navigation, offline, provider-rights, component-version, and route-version evidence before client/public use.",
          ],
          sources: required.map(([doc, section]) => {
            const row = byKey.get(`${doc}/${section}`);
            return {
              document: doc,
              section_key: section,
              title: row.title,
              version: Number(row.version),
              body_text: row.body && row.body.text ? row.body.text : "",
            };
          }),
        };
      },
    },

    "doctrine-sections": {
      description: "Batch-read up to 50 sections by id — current body, version, review state — in one round trip. The fleet read path; never loop read-doctrine per section.",
      inputSchema: { type: "object", properties: {
        section_ids: { type: "array", items: { type: "string" }, maxItems: 50 } },
        required: ["section_ids"] },
      handler: async (c, actor, args) => {
        await actorId(c, actor);
        if ((args.section_ids || []).length > 50)
          throw new ToolError({ error: "batch_too_large", max: 50, got: (args.section_ids || []).length });
        const r = await c.query(
          `select s.id, s.section_key, s.title, s.ordinal, s.status, s.current_version,
                  s.review_after, d.slug as doc_slug, d.content_class, d.visibility,
                  d.owner_actor_id, o.slug as owner_slug, rev.body, rev.content_hash
             from doctrine_section s
             join doctrine_document d on d.id = s.document_id
             left join doctrine_revision rev on rev.id = s.current_revision_id
            left join actor o on o.id = d.owner_actor_id
            where s.id = any($1::uuid[])`, [args.section_ids]);
        // Both partners may read personal sections; owner_slug says whose.
        const visible = r.rows;
        const found = new Set(visible.map(x => x.id));
        return { ok: true, sections: visible.map(({ owner_actor_id, ...x }) => x),
                 missing: args.section_ids.filter(id => !found.has(id)) };
      },
    },

    "search-doctrine": {
      description: "Deterministic doctrine search: weighted title/body FTS unioned with human-approved situation concepts. Returns current section text plus reconstructible score provenance; never generated prose. The versioned active default policy row controls ranking. When no section matches every query word, a second any-word pass answers instead: the result then carries fallback:true (top level and per row) and is best-effort leads, not a confident match — and the query is still logged as a miss so curation can learn a phrase from it. A zero-hit result with fallback machinery live means the store genuinely holds nothing nearby.",
      inputSchema: { type: "object", properties: {
        q: { type: "string" },
        content_classes: { type: "array", items: { type: "string" } },
        limit: { type: "integer" } },
        required: ["q"] },
      handler: async (c, actor, args) => {
        return searchDoctrineSituations(c, actor, args);
      },
    },

    "doctrine-index": {
      description: "The document catalog: every doctrine document with its class, section count, staleness. The store's answer to the old INDEX.md files — derived, never authored.",
      inputSchema: { type: "object", properties: {
        content_classes: { type: "array", items: { type: "string" } } } },
      handler: async (c, actor, args) => {
        await actorId(c, actor);
        const r = await c.query(
          `select d.id, d.slug, d.title, d.content_class, d.visibility, d.updated_at,
                  d.owner_actor_id,
                  count(s.id) filter (where s.status='active') as sections,
                  bool_or(s.review_after < now()) as any_stale
             from doctrine_document d
             left join doctrine_section s on s.document_id = d.id
            where ($1::text[] is null or d.content_class = any($1))
            group by d.id order by d.content_class, d.slug`,
          [args.content_classes || null]);
        // Personal documents are listed for both partners (decision 9c06bf1e),
        // named by owner. Resolved separately: carr_reader reads actor (id, slug).
        const ownerIds = [...new Set(r.rows.map(x => x.owner_actor_id).filter(Boolean))];
        const owners = ownerIds.length ? new Map((await c.query(
          `select id, slug from actor where id = any($1::uuid[])`, [ownerIds])).rows
          .map(o => [o.id, o.slug])) : new Map();
        return { ok: true, documents: r.rows.map(({ owner_actor_id, ...x }) =>
          ({ ...x, owner_slug: owner_actor_id ? owners.get(owner_actor_id) ?? null : null })) };
      },
    },

    "claim-doctrine-sections": {
      write: true,
      description: "Claim sections before a long edit (P3): a cooperative, expiring hint that warns other writers off before they spend tokens on overlapping work. TTL default 300s, max 1800s; a foreign unexpired claim blocks writes via the claim gate; expired claims are free automatically. NEVER a correctness mechanism — base_version is. Renew by claiming again; release when done.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        section_ids: { type: "array", items: { type: "string" } },
        purpose: { type: "string" },
        ttl_seconds: { type: "integer" } },
        required: ["idempotency_key", "section_ids", "purpose"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "claim-doctrine-sections", args, async () => {
        const ttl = Math.min(Math.max(args.ttl_seconds || 300, 30), 1800);
        const claims = [], denied = [];
        for (const id of args.section_ids) {
          const cur = await c.query(
            `select holder_actor_id, holder_session_key, expires_at from doctrine_claim
              where section_id=$1 and expires_at > now()`, [id]);
          if (cur.rows.length && cur.rows[0].holder_actor_id !== actor.id) {
            denied.push({ section_id: id, expires_at: cur.rows[0].expires_at });
            continue;
          }
          await c.query(
            `insert into doctrine_claim (section_id, holder_actor_id, holder_session_key, purpose, expires_at)
             values ($1,$2,$3,$4, now() + ($5 || ' seconds')::interval)
             on conflict (section_id) do update
               set holder_actor_id=$2, holder_session_key=$3, purpose=$4,
                   expires_at = now() + ($5 || ' seconds')::interval, created_at=now()`,
            [id, actor.id, actor.client_id || "unkeyed", args.purpose, String(ttl)]);
          claims.push({ section_id: id, ttl_seconds: ttl });
        }
        return { ok: true, claims, denied };
      }),
    },

    "release-doctrine-claims": {
      write: true,
      description: "Release claims you hold — the done-editing half of claim-doctrine-sections. Releasing a claim you don't hold is a no-op, reported honestly.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        section_ids: { type: "array", items: { type: "string" } } },
        required: ["idempotency_key", "section_ids"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "release-doctrine-claims", args, async () => {
        const r = await c.query(
          `delete from doctrine_claim where section_id = any($1::uuid[]) and holder_actor_id = $2
           returning section_id`, [args.section_ids, actor.id]);
        const released = r.rows.map(x => x.section_id);
        return { ok: true, released,
                 not_held: args.section_ids.filter(id => !released.includes(id)) };
      }),
    },

    "change-doctrine-sections": {
      write: true,
      description: "Write SEVERAL sections of one document atomically — all commit or none do (the half-applied-rename preventer; P2's known gap, closed in P3). Each item carries its own base_version; any stale version or blocked gate aborts the whole set. One generation bump, one snapshot rebuild.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        document: { type: "string" },
        commit_message: { type: "string" },
        items: { type: "array", items: { type: "object", properties: {
          section_key: { type: "string" }, title: { type: "string" },
          body_text: { type: "string" }, base_version: { type: "integer" },
          ordinal: { type: "integer" } },
          required: ["section_key", "body_text", "base_version"] } } },
        required: ["idempotency_key", "document", "items"] },
      handler: async (c, actor, args) => {
        // Reject malformed nested input before the envelope opens its transaction
        // or performs the idempotency read; raw items must never reach the sort.
        validateChangeDoctrineItems(args.items);
        return withEnvelope(c, actor, "change-doctrine-sections", args, async () => {
        if (!args.items.length) throw new ToolError({ error: "empty_change_set" });
        const doc = await resolveDoc(c, args.document);
        if (doc.visibility === "personal" && doc.owner_actor_id !== actor.id)
          throw new ToolError({ error: "personal_doc_not_owner" });
        const cs = await c.query(
          `insert into doctrine_change_set (actor_id, session_key, idempotency_key, state)
           values ($1,$2,$3,'prepared') returning id`,
          [actor.id, actor.client_id || null, args.idempotency_key]);
        // The document's own declared review policy, read once for the whole
        // batch. write-doctrine-section has always applied this; this door did
        // not, so every section written here landed with a null review_after and
        // stayed permanently unwatched — three of them from the 2026-08-12
        // council pass, found by the nightly run's never-reviewed row. Null stays
        // null when the document carries no policy, which is the same
        // else-branch the single-section door uses and the one migration 0097
        // argued is correct: a document with no policy should not gain a clock.
        const policyDays = (await c.query(
          `select p.max_age_days from doctrine_document d
             left join doctrine_review_policy p on p.id = d.review_policy_id
            where d.id=$1`, [doc.id])).rows[0].max_age_days;
        const results = [];
        // Lock in stable section_key order — the deadlock-prevention rule from
        // the council's commit protocol.
        const items = [...args.items].sort((a, b) => a.section_key.localeCompare(b.section_key));
        for (const it of items) {
          let sec = (await c.query(
            `select * from doctrine_section where document_id=$1 and section_key=$2 for update`,
            [doc.id, it.section_key])).rows[0];
          if (sec && Number(sec.current_version) !== it.base_version)
            throw await versionConflict(c, sec);
          if (!sec && it.base_version !== 0)
            throw new ToolError({ error: "section_not_found_base_nonzero",
              section_key: it.section_key });
          const body = { text: it.body_text };
          await runGates(c, actor, "write", { section_id: sec ? sec.id : null, body,
            content_class: doc.content_class, visibility: doc.visibility,
            owner_actor_id: doc.owner_actor_id, doc_slug: doc.slug },
            { changeSetId: cs.rows[0].id });
          if (!sec) {
            const ord = it.ordinal ?? (await c.query(
              `select coalesce(max(ordinal),0)+10 as o from doctrine_section where document_id=$1`,
              [doc.id])).rows[0].o;
            sec = (await c.query(
              `insert into doctrine_section (document_id, section_key, title, ordinal)
               values ($1,$2,$3,$4) returning *`,
              [doc.id, it.section_key, it.title || null, ord])).rows[0];
          } else if (it.title) {
            await c.query(`update doctrine_section set title=$2 where id=$1`, [sec.id, it.title]);
          }
          const hash = await sha256hex(it.body_text);
          const newVersion = Number(sec.current_version) + 1;
          await c.query(
            `insert into doctrine_change_item (change_set_id, section_id, op, expected_version, proposed_body)
             values ($1,$2,'write',$3,$4)`,
            [cs.rows[0].id, sec.id, it.base_version, JSON.stringify(body)]);
          const rev = await c.query(
            `insert into doctrine_revision (section_id, version, parent_revision_id, change_set_id,
               actor_id, session_key, body, plain_text, content_hash, commit_message)
             values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) returning id`,
            [sec.id, newVersion, sec.current_revision_id, cs.rows[0].id, actor.id,
             actor.client_id || null, JSON.stringify(body), it.body_text, hash,
             args.commit_message || null]);
          await c.query(
            `update doctrine_section set current_revision_id=$2, current_version=$3,
                    body_hash=$4, updated_at=now(),
                    review_after = case when $5::int is not null
                                        then now() + ($5::int || ' days')::interval
                                        else review_after end
              where id=$1`,
            [sec.id, rev.rows[0].id, newVersion, hash, policyDays]);
          results.push({ section_key: it.section_key, section_id: sec.id, version: newVersion });
        }
        await c.query(
          `update doctrine_change_set set state='committed', committed_at=now() where id=$1`,
          [cs.rows[0].id]);
        const generation = await bumpGeneration(c);
        await rebuildSnapshot(c, doc.id, generation);
        await writeEvent(c, actor, "change-doctrine-sections", "doctrine_document", doc.id,
          { new: { sections: results.length, keys: results.map(r => r.section_key) },
            idempotency_key: args.idempotency_key });
        return { ok: true, change_set_id: cs.rows[0].id, sections: results, generation };
        });
      },
    },

    "resolve-doctrine-rules": {
      description: "Resolve which doctrine sections are OPERATIVE given the live edge graph (P3): live OVERRIDES/SUPERSEDES suppress their targets, EXCEPTION_TO reports as a scoped carve-out on its target, and the trace names why each suppressed section lost. Fail-closed posture: a cycle in the walked set errors rather than guessing. Scope with content_classes or seed_section_ids.",
      inputSchema: { type: "object", properties: {
        content_classes: { type: "array", items: { type: "string" } },
        seed_section_ids: { type: "array", items: { type: "string" } } },
        },
      handler: async (c, actor, args) => {
        await actorId(c, actor);
        const cands = (await c.query(
          `select s.id, s.section_key, s.title, s.current_version, d.slug as doc_slug,
                  d.content_class
             from doctrine_section s join doctrine_document d on d.id = s.document_id
            where s.status = 'active'
              and (d.visibility <> 'personal' or d.owner_actor_id = $1)
              and ($2::text[] is null or d.content_class = any($2))
              and ($3::uuid[] is null or s.id = any($3))`,
          [actor.id, args.content_classes || null, args.seed_section_ids || null])).rows;
        const ids = cands.map(x => x.id);
        if (!ids.length) return { ok: true, operative: [], suppressed: [], exceptions: [] };
        const edges = (await c.query(
          `select source_section_id, target_section_id, edge_type, scope
             from doctrine_edge
            where retired_by_revision_id is null
              and edge_type in ('OVERRIDES','SUPERSEDES','EXCEPTION_TO')
              and target_section_id = any($1::uuid[])`, [ids])).rows;
        const byId = Object.fromEntries(cands.map(x => [x.id, x]));
        const suppressed = [], exceptions = [];
        const suppressedIds = new Set();
        for (const e of edges) {
          if (e.edge_type === "EXCEPTION_TO") {
            exceptions.push({ target: byId[e.target_section_id],
              exception_section_id: e.source_section_id, scope: e.scope });
            continue;
          }
          // a suppressor only counts while it is itself active
          const srcActive = (await c.query(
            `select 1 from doctrine_section where id=$1 and status='active'`,
            [e.source_section_id])).rows.length;
          if (srcActive && !suppressedIds.has(e.target_section_id)) {
            suppressedIds.add(e.target_section_id);
            suppressed.push({ ...byId[e.target_section_id],
              lost_to: e.source_section_id, via: e.edge_type });
          }
        }
        return { ok: true,
          operative: cands.filter(x => !suppressedIds.has(x.id)),
          suppressed, exceptions };
      },
    },

    "standing-context": {
      description: "THE SESSION BRIEFING VERB (the #219 design, P6 of the doctrine-store build): everything a session must load before working, served from the store — the taught rules (shared + this partner's personal set, with the counts to recite), pending action-required items, and the doctrine catalog pointer. This replaces file-based rule loading: a session that calls this needs no compiled-rules file, no vault read, no Drive. Call it FIRST in any session; recite the counts back to the partner. DEFAULT detail is `gist` — one line per rule, which is what a boot call can actually hold. Pull full text for the handful you need with rule_ids, or everything with detail=full (large; see the payload note in the handler).",
      inputSchema: { type: "object", properties: {
        detail: { type: "string", enum: ["gist", "full", "boot"], description: "gist (DEFAULT) = one line per rule, no human_quote. full = every rule's complete text; ~180KB at 147 rules, which overflows a tool result on most clients. Prefer gist + rule_ids. boot = the gated RULE BOOT: the full text of every always-on rule plus a one-line index of every active rule, paged (see `page`); hooks/rule-boot-gate.py denies other tools in a context until it has fetched every page of the current digest." },
        page: { type: "integer", minimum: 1, description: "detail=boot only: which page of the rule boot to return (1-based, DEFAULT 1). Each response carries pages_total and the exact next call; read every page." },
        rule_ids: { type: "array", items: { type: "string" }, description: "Short ids (the 8-char form the gist prints, e.g. '4e104d4c'). These rules come back in FULL regardless of detail — the lookup path for 'read the binding text before acting on a gist'." },
        workflow: { type: "string", description: "Optional current workflow key used to select applicable typed constraints after registry activation. It is ALSO read as a rule pack name: pass the pack the observed work belongs to and its rules compile into the payload. `rule_delivery.pack_index` lists the names and what triggers each." },
        packs: { type: "array", items: { type: "string" }, description: "Rule packs to compile, when the work spans more than one (git AND a deal, say). Loading is MONOTONIC by design — a pack adds rules and never removes Layer 0. Unknown names come back in rule_delivery.packs_not_found rather than silently loading nothing." },
        surface: { type: "string", description: "Optional execution surface used to select applicable typed constraints; defaults to the authenticated runtime." },
        tier: { type: "string", description: "Optional work tier used to select applicable typed constraints." } },
        },
      handler: async (c, actor, args) => {
        // Personal rules come only from the verified sponsor embedded in the
        // authenticated actor. `partner` was deliberately removed from this
        // schema: a tool argument must never select another human's brain.
        if (Object.prototype.hasOwnProperty.call(args || {}, "partner")) {
          throw new ToolError({ error: "partner_not_selectable",
            hint: "personal scope is derived from the authenticated server-side sponsor; a tool argument cannot select Joe or Dell." });
        }
        if (Object.prototype.hasOwnProperty.call(args || {}, "tenant_id") ||
            Object.prototype.hasOwnProperty.call(args || {}, "organization_tenant_id")) {
          throw new ToolError({ error: "tenant_not_selectable",
            hint: "organization tenant is derived by the server; a tool argument cannot select another tenant." });
        }
        const scope = personalScopeForActor(actor);
        if (scope.status === "error") {
          throw new ToolError({ error: scope.error,
            hint: "this authenticated runtime expected a server-derived sponsoring human. Reconnect through the registered OAuth flow; do not supply a partner argument." });
        }
        const who = scope.sponsor;
        // THE GATED RULE BOOT (mcp-server/src/rule-boot.js). Its own branch,
        // ahead of every other read: it needs only the sponsor-scoped rule
        // rows (same filter as the recitation below) and must stay cheap,
        // because every session and subagent fetches every page of it.
        if (args.detail === "boot") {
          const bootRows = (await c.query(
            `select id, statement, personal_to
               from v_compiled_rules
              where (personal_to is null or ($1::text is not null and personal_to = $1))
                and coalesce(scope->>'kind','') <> 'intro_politics'
              /* standing-context:rule-boot */`, [who])).rows;
          const bootSponsor = scope.status === "personal" ? who : null;
          const boot = await ruleBootPage(bootRows, bootSponsor, args.page ?? 1);
          if (boot.error) {
            throw new ToolError({ error: boot.error, page: args.page,
              pages_total: boot.pages_total, digest: boot.digest,
              hint: `pages run 1..${boot.pages_total}; start again at page 1` });
          }
          return { ok: true, rule_boot: boot,
            identity: { sponsoring_human_id: who,
              personal_brain_scope: bootSponsor ? `${bootSponsor}-personal` : "none" } };
        }
        const detail = args.detail === "full" ? "full" : "gist";
        const wanted = new Set((args.rule_ids || []).map(s => String(s).trim().toLowerCase()));
        const requestedPacks = [...new Set(
          [...(Array.isArray(args.packs) ? args.packs : []),
           ...(args.workflow ? [args.workflow] : [])]
            .map(s => String(s || "").trim().toLowerCase()).filter(Boolean))];
        // Same filter set as the compiled-rules exporter (_fetch_rules +
        // _is_intro, ORDER 37): intro-politics rules render to their own intro
        // file and are NOT part of the recited counts — the verb's numbers
        // must match the files' numbers exactly or the recitation audit
        // (rule 4f7c348f) breaks the day a session compares them.
        // These four stable baseline reads used to be sequential network
        // round trips. Keep the independently fail-soft reads below separate:
        // carr_reader intentionally cannot read rule, so absorbing the proposed-
        // rule query into this mandatory batch would turn its safe denial into a
        // standing-context failure. Take one snapshot only for the core corpus,
        // action queue, and generation counter.
        // The three requests below are independent. The Neon HTTP client pays
        // one public-network round trip per awaited query, so the old serial
        // path accumulated avoidable waits. Keep the proposed-rule denial in
        // its own fail-soft request, combine the optional guidance/delivery
        // reads into one coherent snapshot, and overlap all three requests.
        const baselinePromise = c.query(
          `select
             (select coalesce(jsonb_agg(to_jsonb(r) - 'activated_at'
                                order by r.personal_to nulls first,r.activated_at,r.statement),
                              '[]'::jsonb)
                from (
                  select statement,human_quote,taught_by,personal_to,scope,id,activated_at
                    from v_compiled_rules
                   where (personal_to is null or ($1::text is not null and personal_to = $1))
                     and coalesce(scope->>'kind','') <> 'intro_politics'
                ) r) as all_rules,
             (select coalesce(jsonb_agg(to_jsonb(a) - 'render_seq' order by a.render_seq),
                              '[]'::jsonb)
                from (
                  select number,title,body,owner,render_seq
                    from loop_item
                   where kind = 'action_required' and status = 'open'
                ) a) as action_required,
             (select generation from doctrine_meta where id=1) as doctrine_generation,
             (select coalesce(jsonb_agg(to_jsonb(d)
                                order by d.caught_by_human desc,d.occurrences desc,d.last_seen desc),
                              '[]'::jsonb)
                from (
                  select defect_class,occurrences,caught_by_human,first_seen,last_seen,
                         sources_unread
                    from v_defect_class
                   order by caught_by_human desc,occurrences desc,last_seen desc
                   limit 5
                ) d) as defect_classes
             /* standing-context:baseline-batch */`,
          [who]);
        const optionalSnapshotPromise = c.query(
          `with guidance_registry as (
             select state,manifest_digest from ops.v_guidance_registry_state limit 1
           ), delivery_registry as (
             select count(distinct map_digest)::integer as map_versions,
                    min(map_digest) as map_digest,
                    count(*)::integer as tagged_rules
               from ops.rule_load_layer
           ), plan as (
             select coalesce(jsonb_agg(to_jsonb(p) order by p.load_layer,p.short_id),'[]'::jsonb) as rows
               from ops.rule_delivery_plan($5,$6) p
           ), packs as (
             select coalesce(jsonb_agg(to_jsonb(i) order by i.pack),'[]'::jsonb) as rows
               from ops.rule_pack_index() i
           )
           select guidance_registry.state,guidance_registry.manifest_digest,
                  case when guidance_registry.state = 'active' then
                    (select coalesce(jsonb_agg(to_jsonb(g)),'[]'::jsonb)
                       from (
                         select source_rule_id as id,statement,human_quote,taught_by,personal_to,
                                scope,guidance_type,is_constitution
                           from ops.standing_guidance($1,$2,$3,$4)
                       ) g)
                  else '[]'::jsonb end as standing_rules,
                  case when guidance_registry.state = 'active' then
                    (select coalesce(jsonb_agg(to_jsonb(p) order by p.guidance_type),'[]'::jsonb)
                       from ops.v_guidance_projection_summary p)
                  else '[]'::jsonb end as projection_summary,
                  (select mode from ops.rule_delivery_policy limit 1) as mode,
                  delivery_registry.map_versions,delivery_registry.map_digest,
                  delivery_registry.tagged_rules,
                  plan.rows as delivery_plan,packs.rows as pack_index
             from guidance_registry cross join delivery_registry cross join plan cross join packs`,
          [who, args.workflow || null, args.surface || actor.slug, args.tier || null,
            scope.status === "personal" ? who : null, requestedPacks])
          .catch(() => ({ rows: [] }));
        const proposedRulesPromise = c.query(
          `select id, statement, taught_by, personal_to, created_at
             from rule
            where status = 'proposed'
              and (personal_to is null or ($1::text is not null and personal_to =
                    retrieval_visibility_actor_id($1)))
            order by created_at`, [who]).catch(() => ({ rows: [] }));
        const baselineResult = await baselinePromise;
        // Existing unit fakes historically return the old rule-row shape for
        // any SQL containing v_compiled_rules. Falling back keeps those tests
        // meaningful and also fails safely if an older proxy rewrites the
        // statement rather than returning the aggregate contract.
        const baseline = baselineResult.rows[0] || {};
        const baselineBatched = Array.isArray(baseline.all_rules);
        const allRules = baselineBatched ? baseline.all_rules : baselineResult.rows;
        // The typed registry deliberately changes what is loaded at boot, not
        // what the recited coverage count means. Until a human activates the
        // registry this path is behavior-identical to the compiled-rule path.
        // A missing migration is also inactive so rolling deploys remain safe.
        const optionalSnapshot = (await optionalSnapshotPromise).rows[0] || {};
        const registryState = optionalSnapshot;
        const registryActive = registryState?.state === "active";
        const standingRules = registryActive && Array.isArray(registryState.standing_rules)
          ? registryState.standing_rules : [];
        const projectionSummary = registryActive && Array.isArray(registryState.projection_summary)
          ? registryState.projection_summary : [];
        // Full is an explicit request for the whole corpus. Short-id lookups
        // likewise remain authoritative even when typed boot loading is active.
        const selectedById = new Map((registryActive && detail !== "full"
          ? standingRules : allRules).map(r => [String(r.id).slice(0, 8).toLowerCase(), r]));
        if (registryActive && detail !== "full" && wanted.size) {
          for (const r of allRules) {
            const id = String(r.id).slice(0, 8).toLowerCase();
            if (wanted.has(id)) selectedById.set(id, r);
          }
        }
        // ── THE DELIVERY PLAN (2026-08-23 rules council, migration 0291) ──────
        //
        // Until now this verb had ONE mode: recite every active rule into every
        // session, whatever that session turned out to be doing. Rule 1fddcffb
        // says the always-read core holds only what every session needs
        // regardless of task, and this verb was the thing violating it at every
        // boot. The council's answer is three load layers — layer0 recited
        // always, `control` delivered by the installed gate that refuses the
        // action, `pack` delivered when the observed work matches a trigger —
        // and a PACK INDEX so an undeclared session can see what it has not
        // loaded.
        //
        // SHADOW FIRST, and that is enforced by a row rather than by this code.
        // Both chairs required a week of running the selector beside full
        // recitation before anything is cut, so ops.rule_delivery_policy.mode
        // starts 'shadow': the plan is computed and REPORTED, the recitation is
        // untouched, and `would_omit` is the evidence the week produces. When
        // Joe flips that row to 'enforced', this same code path delivers the
        // scoped set. No deploy sits between the decision and the behaviour, in
        // either direction.
        //
        // EVERY QUERY HERE FAILS SOFT. A worker running ahead of migration 0291
        // must behave exactly as it did before, not error at the one verb every
        // session calls first.
        // One statement means policy, map digest, selector rows, and pack index
        // describe the same database snapshot. Four independent reads under
        // READ COMMITTED could otherwise advertise one map while delivering a
        // plan compiled from another during a registry sync.
        const deliverySnapshot = optionalSnapshot;
        const deliveryMode = deliverySnapshot.mode || null;
        const deliveryMapVersions = Number(deliverySnapshot.map_versions || 0);
        const deliveryMapDigest = deliveryMapVersions === 1
          && /^[0-9a-f]{64}$/i.test(String(deliverySnapshot.map_digest || ""))
          ? `sha256:${String(deliverySnapshot.map_digest).toLowerCase()}` : null;
        const deliveryTaggedRules = Number(deliverySnapshot.tagged_rules || 0);
        const plan = deliveryMode && Array.isArray(deliverySnapshot.delivery_plan)
          ? deliverySnapshot.delivery_plan : [];
        const packIndex = deliveryMode && Array.isArray(deliverySnapshot.pack_index)
          ? deliverySnapshot.pack_index : [];
        const knownPacks = new Set(packIndex.map(p => p.pack));
        const packsNotFound = requestedPacks.filter(p => !knownPacks.has(p));
        const emptyPacks = requestedPacks.filter(pack => {
          const row = packIndex.find(item => item.pack === pack);
          return row && Number(row.rule_count) < 1;
        });
        const selectedShortIds = new Set(
          plan.filter(r => r.selected).map(r => String(r.short_id).toLowerCase()));
        // "Null workflow returns Layer 0, never all 204 and never nothing" — the
        // second half of that sentence is this line. An empty plan means the tags
        // are not installed here, and an empty selection means the compiler found
        // nothing to hand over; either way the honest fallback is the full set,
        // said out loud, rather than a session that boots with no rules at all.
        const deliveryUsable = plan.length > 0 && selectedShortIds.size > 0
          && deliveryMapVersions === 1 && deliveryTaggedRules > 0 && deliveryMapDigest !== null
          && packsNotFound.length === 0 && emptyPacks.length === 0;
        const enforcing = deliveryMode === "enforced" && deliveryUsable;
        // Intro-politics rules deliberately do not belong to the full boot
        // corpus above, but they DO make up the vendor-intros pack.  Building
        // selectedShortIds from 219 rows and then filtering only the 205-row
        // recitation pool made that pack a phantom: reported selected, never
        // deliverable.  In enforcement only, widen the candidate pool; the
        // selected ids still decide whether any intro rule actually arrives.
        const deliveryRulePool = enforcing ? (await c.query(
          `select statement, human_quote, taught_by, personal_to, scope, id
             from v_compiled_rules
            where (personal_to is null or ($1::text is not null and personal_to = $1))
            order by personal_to nulls first, activated_at, statement`, [who])).rows : allRules;

        // TWO SCOPINGS MUST NOT MULTIPLY. When the typed guidance registry is
        // active, `selectedById` above already holds a NARROWED set — constitution
        // plus applicable constraints — and layering delivery on top of that
        // would compose two independent narrowings into something smaller than
        // either, including smaller than Layer 0. So while delivery is
        // enforcing, every Layer 0 rule is put back from the full set before any
        // filtering. Layer 0 is the floor of a boot in both designs, and a floor
        // that two correct mechanisms can quietly sink through is not a floor.
        if (enforcing) {
          for (const r of deliveryRulePool) {
            const id = String(r.id).slice(0, 8).toLowerCase();
            if (selectedShortIds.has(id) && !selectedById.has(id)) selectedById.set(id, r);
          }
        }
        const rules = [...selectedById.values()];
        const shared = rules.filter(r => !r.personal_to);
        const personal = rules.filter(r => r.personal_to);
        const allShared = allRules.filter(r => !r.personal_to);
        const allPersonal = allRules.filter(r => r.personal_to);
        // A rule the caller asked for by id is ALWAYS returned. Scoping decides
        // what arrives unasked; it never removes the lookup path that rule
        // d5dcfe26 and the gist hint both send sessions down.
        const deliverable = (r) => {
          const id = String(r.id).slice(0, 8).toLowerCase();
          return !enforcing || detail === "full" || wanted.has(id)
                 || selectedShortIds.has(id);
        };
        const deliveredShared = shared.filter(deliverable);
        const deliveredPersonal = personal.filter(deliverable);
        const omitted = enforcing
          ? [...shared, ...personal].filter(r => !deliverable(r))
              .map(r => String(r.id).slice(0, 8))
          : plan.filter(r => !r.selected).map(r => r.short_id);
        const actionReq = baselineBatched ? baseline.action_required : (await c.query(
          `select number, title, body, owner
             from loop_item
            where kind = 'action_required' and status = 'open'
            order by render_seq`).catch(() => ({ rows: [] }))).rows;
        // PROPOSED RULES, surfaced here because nowhere else surfaces them.
        // Loop #149, Joe 2026-08-03: "I had no idea they were even proposed
        // which is a problem." teach() writes a rule as PROPOSED and activation
        // is a human decision by design — a good design with one hole: the human
        // who has to decide is never told there is anything to decide. The
        // compiled-rules exports read ACTIVE only, so a proposed rule is
        // invisible on every surface a partner reads, and sits there until
        // someone happens to look.
        //
        // It belongs in THIS payload rather than a health row because this verb
        // is the opening act of every session, so the question reaches whoever
        // is working on the day it is asked. Kept to id, gist and who taught it:
        // the binding text is one standing-context call away with rule_ids, and
        // a wall of unapproved prose at session start would be skimmed like
        // every other wall.
        const proposedRules = (await proposedRulesPromise).rows;
        // THE DEFECT CLASSES (0103, loop #185). Surfaced HERE for the same reason the
        // proposed rules are: this verb is the opening act of every session, and the
        // loop's acceptance criterion is literally "a session can be told at start
        // 'this class has failed N times, here are the artifacts that were stale'"
        // rather than being handed the prose rules and trusted.
        //
        // Ranked by how many a HUMAN had to catch, not by raw count. A class the system
        // catches itself is working as designed; a class a partner keeps finding is the
        // one that should change how this session reads today. Capped at five, because a
        // wall at session start is skimmed like every other wall.
        const defectClasses = baselineBatched && Array.isArray(baseline.defect_classes)
          ? baseline.defect_classes : [];
        const gen = baselineBatched
          ? { generation: baseline.doctrine_generation }
          : (await c.query(`select generation from doctrine_meta where id=1`)).rows[0];
        // PAYLOAD NOTE (2026-08-08, Joe's yes): detail=full returned ~183KB at
        // 147 rules and overflowed the tool-result limit on the very first live
        // call — the verb that is the OPENING ACT of every session could not be
        // read by a session. Gist is therefore the default.
        //
        // The cut below is a PAYLOAD CAP, not a second source of truth for gist
        // wording. exporters/targets.py `_gist` remains the authority for the
        // rendered files; this shares its constants and was verified character-
        // identical against it across all 147 live rules on 2026-08-08 (0
        // mismatches) — re-run that comparison if either side changes. And
        // the counts contract above (rule 4f7c348f) is about the NUMBERS, which
        // are unaffected by detail mode. If the file gist ever moves into the
        // store as a derived column, delete this and read that column instead.
        const GIST_STOPS = [". ", "; ", ": "], MIN_GIST = 40, MAX_GIST = 110;
        const gist = (statement) => {
          const s0 = String(statement || "").split(/\s+/).join(" ");
          const stripped = s0.replace(/\s*\([^()]*\)/g, "").split(/\s+/).join(" ");
          const s = stripped.length >= MIN_GIST ? stripped : s0;
          let cut = s.length;
          for (const stop of GIST_STOPS) {
            const i = s.indexOf(stop, MIN_GIST);
            if (i !== -1) cut = Math.min(cut, i);
          }
          let g = s.slice(0, cut).replace(/[ ,;:.]+$/, "");
          if (g.length > MAX_GIST) {
            g = g.slice(0, MAX_GIST).replace(/\s+\S*$/, "").replace(/[ ,;:.—-]+$/, "") + "…";
          }
          return g;
        };
        // Scoped boot returns short gists for the always-present Layer 0.
        // Jev and the pack/trigger index fetch binding text when the task needs
        // it. An explicit detail=full or rule_ids lookup still returns full text.
        const shape = (r, withQuote) => {
          const id = String(r.id).slice(0, 8);
          const idLower = id.toLowerCase();
          if (detail === "full" || wanted.has(idLower)) {
            return withQuote
              ? { id, statement: r.statement, taught_by: r.taught_by, human_quote: r.human_quote }
              : { id, statement: r.statement, human_quote: r.human_quote };
          }
          return { id, gist: gist(r.statement) };
        };
        const missing = [...wanted].filter(
          w => !allRules.some(r => String(r.id).slice(0, 8).toLowerCase() === w));
        const constitutionLoaded = standingRules.filter(r => r.is_constitution).length;
        const constraintLoaded = standingRules.filter(
          r => r.guidance_type === "constraint" && !r.is_constitution).length;
        const constitutionByType = new Map();
        for (const r of standingRules.filter(r => r.is_constitution)) {
          constitutionByType.set(r.guidance_type,
            (constitutionByType.get(r.guidance_type) || 0) + 1);
        }
        const deferredByType = Object.fromEntries(projectionSummary
          .filter(r => r.guidance_type !== "constraint")
          .map(r => [r.guidance_type,
            Math.max(0, Number(r.active_items) - (constitutionByType.get(r.guidance_type) || 0))])
          .filter(([, count]) => count > 0));
        const countText = (delivered, available) => delivered === available
          ? String(available) : `${delivered} of ${available}`;
        const reciteCounts = scope.status === "personal"
          ? `Rules loaded: ${countText(deliveredShared.length, allShared.length)} shared, `
            + `${countText(deliveredPersonal.length, allPersonal.length)} ${who}-personal`
            + (enforcing ? ` (Layer 0${requestedPacks.length ? " + " + requestedPacks.join(", ") : ""})` : "")
          : `Rules loaded: ${countText(deliveredShared.length, allShared.length)} shared, `
            + "0 personal (unsponsored runtime)";
        // Shadow diagnostics stay bounded. Rule text is fetched only when a
        // task selects it; the former full-text preview dominated boot context.
        let corePreview;
        if (deliveryMode === "shadow") {
          const foundIds = new Set(allRules
            .filter(r => CORE_RULE_ID_SET.has(String(r.id).slice(0, 8).toLowerCase()))
            .map(r => String(r.id).slice(0, 8).toLowerCase()));
          corePreview = {
            source: CORE_RULE_TRIAGE_SOURCE,
            core_rule_count: CORE_RULE_COUNT,
            core_rules_found: foundIds.size,
            missing_core_ids: CORE_RULE_IDS.filter(id => !foundIds.has(id)),
          };
        }
        return { ok: true,
          ...(corePreview ? { core_preview: corePreview } : {}),
          recite: reciteCounts,
          identity: {
            organization_tenant_id: organizationTenantForActor(actor),
            sponsoring_human_id: who,
            agent_principal_id: actor.slug,
            runtime_principal: actor.slug,
            personal_brain_scope: scope.status === "personal" ? `${who}-personal` : "none",
            personal_scope_source: scope.source,
            session_capability_profile: actor.authorization_class || "unknown",
            operational_profile: actor.operational_profile || "full",
            human_only_authority: actor.human === true,
          },
          detail,
          ...(missing.length ? { rule_ids_not_found: missing } : {}),
          ...(detail === "gist" ? { hint_full_text:
            "One line per rule. NEVER quote a gist as the rule — call standing-context again with rule_ids:['<id>',…] for the binding text before acting on one." } : {}),
          shared_rules: deliveredShared.map(r => shape(r, true)),
          personal_rules: deliveredPersonal.map(r => shape(r, false)),
          ...(deliveryMode ? { rule_delivery: {
            mode: deliveryMode,
            enforcing,
            say: enforcing
              ? "Layer 0 plus the packs this session declared. The rest is one "
                + "standing-context call away with packs:[…]."
              : "SHADOW: the scoped selector ran beside the full recitation and "
                + "changed nothing. `would_omit` is what a scoped boot would not "
                + "have handed you — if you needed one of those rules this turn, "
                + "that is the miss the shadow week exists to find.",
            declared_packs: requestedPacks,
            map_digest: deliveryMapDigest,
            map_versions: deliveryMapVersions,
            tagged_rules: deliveryTaggedRules,
            ...(packsNotFound.length ? { packs_not_found: packsNotFound,
              hint: "an unknown pack loads nothing; check pack_index for the name" } : {}),
            ...(emptyPacks.length ? { empty_packs: emptyPacks } : {}),
            layer0: plan.filter(r => r.load_layer === "layer0").length,
            selected: selectedShortIds.size,
            in_scope: plan.length,
            would_omit_count: omitted.length,
            would_omit: omitted,
            ...(deliveryMode && !deliveryUsable ? { fallback:
              "no usable coherent delivery plan (the map, tags, or requested packs are absent), so the "
              + "FULL set was recited — a scoped boot never returns nothing" } : {}),
            pack_index: packIndex.map(p => ({ pack: p.pack, title: p.title,
              triggers: p.triggers, rules: Number(p.rule_count) })),
            hint: "Packs load on OBSERVED WORK, never on the session's name "
              + "(rule 347a9ca6). If this turn drifted into git, a deal or a "
              + "surface, call standing-context again with that pack.",
          } } : {}),
          ...(registryActive ? { guidance_registry: {
            state: "active",
            manifest_digest: registryState.manifest_digest,
            loaded: {
              constitution: constitutionLoaded,
              applicable_constraints: constraintLoaded,
            },
            deferred_by_type: deferredByType,
            projection_digests: Object.fromEntries(projectionSummary.map(
              r => [r.guidance_type, r.projection_digest])),
            hint: "Non-standing guidance is loaded at the work surface: use search-doctrine-situations for doctrine, and the typed procedure, rubric, preference, precedent, and example projections for their matching workflows.",
          } } : {}),
          ...(defectClasses.length ? { known_failure_classes: {
            say: "ways this system has actually been wrong before, worst first — " +
                 "not a warning, a reading list",
            classes: defectClasses.map(d => ({
              defect_class: d.defect_class,
              occurrences: d.occurrences,
              caught_by_human: d.caught_by_human,
              // A bare date, not a timestamp with a timezone name in it — this is read
              // at a glance at session start (rule 80def9d2).
              last_seen: d.last_seen instanceof Date
                ? d.last_seen.toISOString().slice(0, 10)
                : String(d.last_seen).slice(0, 10),
              sources_that_went_unread: d.sources_unread || [],
            })),
            hint: "before asserting the state of anything dated, check whether you are " +
                  "about to repeat one of these. File a new one with record-defect the " +
                  "moment you or a partner catches an error — including your own.",
          } } : {}),
          ...(proposedRules.length ? { awaiting_activation: {
            count: proposedRules.length,
            say: `${proposedRules.length} rule(s) are written and waiting on a yes — they bind nobody until activated`,
            rules: proposedRules.map(r => ({
              id: String(r.id).slice(0, 8),
              gist: gist(r.statement),
              scope: r.personal_to ? `${r.personal_to}-personal` : "shared",
              taught_by: r.taught_by,
              proposed_on: r.created_at ? String(r.created_at).slice(0, 10) : null })),
            hint: "read the full text with rule_ids, then approve-rule or retire-rule. "
                + "Surfacing these is loop #149: a rule nobody knows is proposed is a "
                + "rule nobody can approve.",
          } } : {}),
          action_required: actionReq,
          doctrine: { generation: Number(gen.generation),
            hint: "doctrine-index for the catalog; search-doctrine / read-doctrine to read — there are no files" } };
      },
    },

    "dry-run-doctrine-gates": {
      write: true,   // persists the run for audit — must ride the writer pool
      description: "Run the validation gates against a proposed write WITHOUT committing anything — the self-check an agent runs before spending a real write, and the shakedown harness for new gates. Persists the run (dry_run=true) for audit.",
      inputSchema: { type: "object", properties: {
        op: { type: "string", description: "create | write | refs_set | retire" },
        document: { type: "string" }, section_id: { type: "string" },
        slug: { type: "string" }, body_text: { type: "string" },
        content_class: { type: "string" },
        links: { type: "array" }, edges: { type: "array" } },
        required: ["op"] },
      handler: async (c, actor, args) => {
        let content_class = args.content_class;
        if (args.document && !content_class)
          content_class = (await resolveDoc(c, args.document)).content_class;
        const res = await runGates(c, actor, args.op, {
          section_id: args.section_id || null, slug: args.slug,
          body: args.body_text !== undefined ? { text: args.body_text } : undefined,
          content_class, links: args.links || [], edges: args.edges || [] },
          { dryRun: true });
        return { ok: true, ...res };
      },
    },
  };
}
