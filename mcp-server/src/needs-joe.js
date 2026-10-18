// needs-joe.js — ONE list of every open item waiting on Joe (gap #10).
//
// Each item is read live from the record that owns it, so it leaves the list
// the moment that record closes: a loop closed, a Work Request out of
// needs_joe, a board question answered, a rule decided, a PR closed. Nothing
// here is stored; there is no second copy to drift.
//
// Sources the Worker cannot see (out/orch/needs-joe-or-wait.txt and the live
// PR state behind it) arrive as the `needs-joe-local` board snapshot, which
// tools/needs_joe_local.py re-derives from scratch on every board run.
//
// Only filer-flagged loops enter the list. Explicit rulings and human-only
// blockers take precedence; text supplies a display class for other items.
// These labels do not grant authority or replace the conduct gate. The parity
// test covers its protected vocabulary and the watchdog's credential phrases;
// extra terms here describe the locally filed asks, including credits and login.
// Prospecting loops remain on the Lead Board.

import { organizationTenantForActor } from "./identity.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";

export const NEEDS_JOE_LOCAL_BOARD = "needs-joe-local";
const JOE = "joe";
const LOCAL_STALE_MS = 2 * 60 * 60 * 1000; // the board job runs every 15 minutes
const DAY_MS = 24 * 60 * 60 * 1000;

const CLASSES = [
  ["credential", /\b(credentials?|log ?ins?|sign(?:ed)?[- ]?(?:in|out)|authenticat\w*|oauth|tokens? expired|not logged in|bad credentials|login required|api keys?|passwords?|face ?id|touch ?id|biometric\w*|2fa|mfa|keychain|secrets?)\b|credential-health probe/i],
  ["money", /\b(spend|pay|paid|payment|invoices?|budget|purchase|fees?|commission|pricing|subscription|subscribe|renews?|renewal|billing|credits)\b|[$£€]\s?\d|\b\d+\s?(usd|dollars?)\b|\b(per|a)\s(month|year|seat|user)\b/i],
  ["outbound", /\b(client|prospect|landlord|listing agent|tenant|vendor|broker|doctor|practice owner|LOI|letter of intent|PSA|lease|proposal|counter|RFP|send|email|publish|post|tweet|linkedin|facebook|instagram)\b/i],
  ["irreversible", /\b(delete|destroy|drop table|force[- ]push|revoke|irreversible)\b/i],
];

const WHY = {
  credential: "Needs Joe's own login, credential or biometric; no session can hold it.",
  money: "Spends or commits money; only Joe moves money.",
  outbound: "Reaches a client or the public; Joe decides what leaves the house.",
  irreversible: "Cannot be undone; Joe decides irreversible acts.",
  ruling: "A ruling only Joe can give.",
  human_only: "The step itself needs a person.",
};

const ACTION = {
  credential: "Sign in or renew it at your keyboard",
  money: "Approve or decline the spend",
  outbound: "Approve or decline what goes out",
  irreversible: "Confirm or stop it",
  ruling: "Give the ruling",
  human_only: "Do the step",
};

export const EXCLUSION_REASONS = {
  internal: "Internal work with no human-only class; the system decides and does it.",
  waiting_on_others: "Waiting on a counterparty, an outside event or another piece of work, not on Joe.",
  system_decidable: "The verb that decides it carries no human-only or authority-only flag.",
  lead_outreach: "Lead outreach lives on the Lead Board, never on a glanceable list.",
};

const TIER_RANK = { work: 0, capability: 1, pending: 2 };

export function classifyHumanOnly(text) {
  for (const [name, pattern] of CLASSES) if (pattern.test(text || "")) return name;
  return null;
}

function plainTitle(text) {
  // Rule 3a9dbafd: a title starts with what the thing is, never a bare id.
  return String(text || "").replace(/^\s*(?:#?\d+|A\d+|WR-\d+)\s*[:.)\-–—]\s*/i, "").trim().slice(0, 200);
}

function ageDays(at, now) {
  const time = Date.parse(at);
  return Number.isFinite(time) ? Math.max(0, Math.floor((now - time) / DAY_MS)) : null;
}

function item(fields, now) {
  const why = fields.why;
  return {
    key: fields.key, source: fields.source, title: plainTitle(fields.title),
    why: { class: why, text: fields.why_text || WHY[why] },
    action: fields.action || ACTION[why], link: fields.link ?? null,
    since: fields.since ?? null, age_days: ageDays(fields.since, now),
    blocks: fields.blocks,
    ...(fields.stale ? { stale: true } : {}),
  };
}

function loopItems(rows, now, exclude) {
  const out = [];
  for (const row of rows) {
    const text = [row.label, row.blocker_detail].filter(Boolean).join(" — ");
    if (row.domain === "prospecting") { exclude("lead_outreach"); continue; }
    if (["counterparty", "external_event", "other_lane"].includes(row.blocker_class)) {
      exclude("waiting_on_others");
      continue;
    }
    // Unflagged work remains internal even when its text mentions a client.
    const flagged = row.kind === "action_required" || row.marker === "decision"
      || ["human_only", "ruling", "capability"].includes(row.blocker_class);
    if (!flagged) { exclude("internal"); continue; }
    const why = row.marker === "decision" || row.blocker_class === "ruling" ? "ruling"
      : row.blocker_class === "human_only" ? "human_only"
        : classifyHumanOnly(text) ?? "human_only";
    const tier = row.unblocks ? "work" : why === "credential" ? "capability" : "pending";
    out.push(item({
      key: `loop:${row.number}`,
      source: row.kind === "action_required" ? "action_required" : "loop",
      title: row.label, why,
      why_text: row.blocker_detail && why !== "credential" ? `${WHY[why]} ${row.blocker_detail}.` : undefined,
      link: null, since: row.created_at,
      blocks: { tier, text: row.unblocks ? `Blocks ${row.unblocks}`
        : tier === "capability" ? "Blocks the automations that use it" : "Nothing is waiting on it yet" },
    }, now));
  }
  return out;
}

function localItems(page, stale, now, exclude) {
  const out = [];
  for (const row of Array.isArray(page?.items) ? page.items : []) {
    if (row.kind === "waiting") { exclude("waiting_on_others"); continue; }
    const pr = row.repo && row.number ? `${row.repo}#${row.number}` : null;
    const why = classifyHumanOnly(row.text) ?? "human_only";
    if (row.kind === "needs_joe" && pr) {
      out.push(item({
        key: `pr:${pr}`, source: "pull_request",
        title: `${row.pr_title || "Pull request"}: ${row.text}`, why,
        link: `https://github.com/${row.repo}/pull/${row.number}`, since: row.at,
        blocks: { tier: "work", text: `Blocks the pull request "${row.pr_title || pr}" from finishing` },
        stale,
      }, now));
    } else if (row.kind === "tabled" || row.kind === "needs_joe") {
      out.push(item({
        key: `tabled:${row.text}`, source: "tabled", title: row.text, why, link: null, since: row.at,
        blocks: why === "credential"
          ? { tier: "capability", text: "Blocks the automations that use it" }
          : { tier: "pending", text: "Parked until Joe is back" },
        stale,
      }, now));
    }
  }
  return out;
}

// The same thing filed twice (a probe that re-files each night) is one thing
// to do. The oldest record leads; `records` names every one so each clears
// with its own record.
function collapseDuplicates(sorted) {
  const byTitle = new Map();
  const out = [];
  for (const entry of sorted) {
    const id = `${entry.source}\u0000${entry.title}`;
    const first = byTitle.get(id);
    if (first) { first.records.push(entry.key); continue; }
    const kept = { ...entry, records: [entry.key] };
    byTitle.set(id, kept);
    out.push(kept);
  }
  return out;
}

async function source(sources, name, load) {
  try {
    const value = await load();
    sources[name] = { state: value.state || "ready", count: value.items.length };
    return value.items;
  } catch {
    sources[name] = { state: "unavailable", count: 0 };
    return [];
  }
}

// `governance` is the queue governance-queue already read; its rule approvals
// join the list and its other lanes are judged by the flag on the verb that
// decides them.
export async function readNeedsJoe(c, actor, governance, { now = Date.now() } = {}) {
  if (partnerAuthoritySlugForActor(actor) !== JOE) return {
    schema: "needs-joe.v1", state: "not_applicable", count: 0, items: [],
    excluded: { count: 0, by_reason: {}, reasons: {} }, sources: {},
  };
  const tenant = organizationTenantForActor(actor);
  const sources = {};
  const excluded = { count: 0, by_reason: {} };
  const exclude = (reason) => {
    excluded.count += 1;
    excluded.by_reason[reason] = (excluded.by_reason[reason] || 0) + 1;
  };

  const found = [
    ...await source(sources, "loops", async () => {
      const rows = (await c.query(
        `select number, kind, owner, domain, marker, blocker_class, blocker_detail, unblocks, created_at,
                coalesce(nullif(title, ''),
                  nullif(regexp_replace(split_part(body, E'\\n', 1), '\\*\\*', '', 'g'), '')) as label
           from loop_item
          where status = 'open' and kind in ('action_required', 'open_loop', 'team_loop')
            and (lower(owner) in ('joe', 'joint') or (owner is null and kind = 'action_required'))
          order by created_at limit 500 /* needs-joe */`)).rows;
      return { items: loopItems(rows, now, exclude) };
    }),
    ...await source(sources, "work_requests", async () => {
      const rows = (await c.query(
        `select ref, title, blocker_detail, updated_at
           from ops.work_request
          where state = 'needs_joe'
            and (organization_tenant_id is null or organization_tenant_id = $1::text)
          order by updated_at /* needs-joe */`, [tenant])).rows;
      return { items: rows.map(row => {
        const why = classifyHumanOnly(row.blocker_detail) ?? "ruling";
        return item({
          key: `work-request:${row.ref}`, source: "work_request", title: row.title, why,
          why_text: row.blocker_detail ? `${WHY[why]} ${row.blocker_detail}.` : undefined,
          action: "Answer the Work Request", link: "https://app.doctorcre.com/work-requests",
          since: row.updated_at, blocks: { tier: "work", text: `Blocks the Work Request "${row.title}"` },
        }, now);
      }) };
    }),
    ...await source(sources, "board_questions", async () => {
      const rows = (await c.query(
        `select q.board_id, q.question_id, q.prompt, q.asked_at
           from board_question q left join board_answer a
             on a.organization_tenant_id = q.organization_tenant_id
            and a.sponsoring_human_slug = q.sponsoring_human_slug and a.board_id = q.board_id
            and a.question_id = q.question_id and a.question_revision = q.revision
          where q.organization_tenant_id = $1 and q.sponsoring_human_slug = $2
            and q.current = true and a.id is null
          order by q.asked_at /* needs-joe */`, [tenant, JOE])).rows;
      return { items: rows.map(row => item({
        key: `board-question:${row.board_id}/${row.question_id}`, source: "board_question",
        title: row.prompt, why: classifyHumanOnly(row.prompt) ?? "ruling",
        action: "Answer it on the progress board",
        link: `https://app.doctorcre.com/control-room/progress/board/${encodeURIComponent(row.board_id)}`,
        since: row.asked_at, blocks: { tier: "work", text: "The session that asked is waiting on the answer" },
      }, now)) };
    }),
    ...await source(sources, "governance", async () => {
      for (const _ of governance.pending_retrieval_proposals || []) exclude("system_decidable");
      const rules = (governance.pending_rule_approvals || []).map(rule => item({
        key: `rule:${rule.rule_id}`, source: "rule_approval",
        title: `Approve or decline the proposed rule: ${String(rule.statement || "").slice(0, 160)}`,
        why: "ruling", why_text: "A proposed rule binds nobody until Joe approves it.",
        action: "Approve or decline the rule", since: rule.admitted_at || rule.taught_at,
        blocks: { tier: "pending", text: "The rule binds nobody until it is decided" },
      }, now));
      const batches = (governance.pending_guidance_import_batches || []).map(batch => item({
        key: `guidance-batch:${batch.batch_id}`, source: "guidance_batch",
        title: `Accept or reject a staged guidance import of ${batch.entry_count} entries${batch.reason ? `: ${batch.reason}` : ""}`,
        why: "ruling", why_text: "Importing guidance is an authority decision.",
        action: "Accept or reject the batch", since: batch.staged_at,
        blocks: { tier: "pending", text: "The guidance stays staged until it is decided" },
      }, now));
      return { items: [...rules, ...batches] };
    }),
    ...await source(sources, "local", async () => {
      const row = (await c.query(
        `select snapshot_json, updated_at from board_snapshot
          where organization_tenant_id = $1 and sponsoring_human_slug = $2 and board_id = $3 /* needs-joe */`,
        [tenant, JOE, NEEDS_JOE_LOCAL_BOARD])).rows[0];
      if (!row) throw new Error("local needs-joe sources were never published");
      const stale = now - Date.parse(row.updated_at) > LOCAL_STALE_MS;
      return { state: stale ? "stale" : "ready", items: localItems(row.snapshot_json, stale, now, exclude) };
    }),
  ];

  const order = (a, b) => TIER_RANK[a.blocks.tier] - TIER_RANK[b.blocks.tier]
    || (b.age_days ?? -1) - (a.age_days ?? -1) || a.key.localeCompare(b.key);
  const items = collapseDuplicates(found.sort(order));
  const states = Object.values(sources).map(value => value.state);
  return {
    schema: "needs-joe.v1",
    state: states.every(state => state === "ready") ? (items.length ? "ready" : "empty") : "partial",
    ordered_by: "what it blocks: live work, then capabilities, then pending approvals; oldest first",
    count: items.length, items,
    excluded: { ...excluded, reasons: Object.fromEntries(
      Object.keys(excluded.by_reason).map(reason => [reason, EXCLUSION_REASONS[reason]])) },
    sources,
  };
}
