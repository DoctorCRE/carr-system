import { ToolError } from "./tool-error.js";
import { organizationTenantForActor } from "./identity.js";
import { NEEDS_JOE_LOCAL_BOARD } from "./needs-joe.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";

const REF = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const BOARD_ANSWER_WRITE_VERBS = new Set([
  "publish-board-snapshot", "ask-board-question", "revise-board-question",
  "answer-board-question", "acknowledge-board-answer", "record-board-answer-applied",
]);
const questionFields = {
  prompt: { type: "string" }, choices: { type: "array", items: { type: "string" } },
  allow_free_text: { type: "boolean" }, default_answer: { type: ["string", "null"] },
  asker_ref: { type: "string" },
};

function ref(value, field) {
  if (typeof value !== "string" || !REF.test(value))
    throw new ToolError({ error: "board_reference_invalid", field });
  return value;
}

function version(value, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum)
    throw new ToolError({ error: "board_base_version_invalid" });
  return value;
}

function answerId(value) {
  if (typeof value !== "string" || !UUID.test(value))
    throw new ToolError({ error: "board_answer_id_invalid" });
  return value;
}

function text(value, field, max = 4000) {
  if (typeof value !== "string" || !value.trim() || value.length > max)
    throw new ToolError({ error: "board_text_invalid", field });
  return value.trim();
}

function question(args) {
  const prompt = text(args.prompt, "prompt");
  const asker_ref = ref(args.asker_ref, "asker_ref");
  const choices = args.choices ?? [];
  if (!Array.isArray(choices) || choices.length > 8 ||
      choices.some(item => typeof item !== "string" || !item.trim() || item.length > 500) ||
      new Set(choices).size !== choices.length)
    throw new ToolError({ error: "board_choices_invalid" });
  const allow_free_text = args.allow_free_text ?? choices.length === 0;
  if (typeof allow_free_text !== "boolean" || (!choices.length && !allow_free_text))
    throw new ToolError({ error: "board_answer_mode_invalid" });
  const default_answer = args.default_answer === undefined || args.default_answer === null
    ? null : text(args.default_answer, "default_answer", 500);
  if (default_answer && choices.length && !allow_free_text && !choices.includes(default_answer))
    throw new ToolError({ error: "board_default_invalid" });
  return { prompt, asker_ref, choices, allow_free_text, default_answer };
}

function statusSql(alias = "a") {
  return `case when ${alias}.id is null then null ` +
    `when ${alias}.applied_at is not null then 'Applied' ` +
    `when ${alias}.received_at is not null then 'Received' else 'Sent' end`;
}

function sponsor(actor) {
  const slug = partnerAuthoritySlugForActor(actor);
  if (!slug) throw new ToolError({ error: "board_sponsor_unavailable" });
  return slug;
}

export function progressBoardSummary(row) {
  const data = row.snapshot_json ?? {};
  const tasks = data.tasks && typeof data.tasks === "object" && !Array.isArray(data.tasks)
    ? Object.values(data.tasks) : [];
  const counts = new Map();
  for (const task of tasks) {
    if (!task || typeof task !== "object" || Array.isArray(task)) continue;
    const status = typeof task.status === "string" && task.status.trim() ? task.status : "queued";
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  return {
    board_id: row.board_id,
    title: typeof data.title === "string" && data.title.trim() ? data.title : row.board_id,
    project: typeof data.project === "string" && data.project.trim() ? data.project : row.board_id,
    updated_at: row.updated_at,
    task_counts: Object.fromEntries([...counts].sort(([a], [b]) => a.localeCompare(b))),
  };
}

export function boardAnswerTools({ withEnvelope, writeEvent }) {
  return {
    "publish-board-snapshot": {
      write: true,
      description: "Publish the current progress board view for the signed-in app. Use base_version 0 to create it; stale versions refuse. The snapshot is display data and never grants authority.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, board_id: { type: "string" },
        base_version: { type: "integer" }, snapshot: { type: "object" },
      }, required: ["idempotency_key", "board_id", "base_version", "snapshot"] },
      handler: async (c, actor, args) => {
        const board = ref(args.board_id, "board_id");
        version(args.base_version);
        if (!args.snapshot || Array.isArray(args.snapshot) || typeof args.snapshot !== "object" ||
            JSON.stringify(args.snapshot).length > 262144)
          throw new ToolError({ error: "board_snapshot_invalid" });
        const principal = sponsor(actor);
        return withEnvelope(c, actor, "publish-board-snapshot", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const rows = args.base_version === 0
            ? await c.query(
              `insert into board_snapshot (organization_tenant_id,sponsoring_human_slug,board_id,version,snapshot_json,updated_by_actor_id)
               values ($1,$2,$3,1,$4::jsonb,$5) on conflict do nothing
               returning id,organization_tenant_id,sponsoring_human_slug,board_id,version,snapshot_json,updated_at`,
              [tenant, principal, board, JSON.stringify(args.snapshot), actor.id])
            : await c.query(
              `update board_snapshot set version=version+1,snapshot_json=$1::jsonb,
                 updated_by_actor_id=$2,updated_at=now()
               where organization_tenant_id=$3 and sponsoring_human_slug=$4 and board_id=$5 and version=$6
               returning id,organization_tenant_id,sponsoring_human_slug,board_id,version,snapshot_json,updated_at`,
              [JSON.stringify(args.snapshot), actor.id, tenant, principal, board, args.base_version]);
          if (!rows.rows.length) throw new ToolError({ error: "board_version_conflict" });
          const snapshot = rows.rows[0];
          await writeEvent(c, actor, "publish-board-snapshot", "board_snapshot", snapshot.id,
            { new: { board_id: board, version: snapshot.version }, idempotency_key: args.idempotency_key });
          return { ok: true, snapshot };
        });
      },
    },

    "ask-board-question": {
      write: true,
      description: "Ask one named board question. The asker reference routes later answers; choices or free text define the answer control.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, board_id: { type: "string" },
        question_id: { type: "string" }, base_version: { type: "integer" }, ...questionFields,
      }, required: ["idempotency_key", "board_id", "question_id", "base_version", "prompt", "asker_ref"] },
      handler: async (c, actor, args) => {
        const board = ref(args.board_id, "board_id");
        const id = ref(args.question_id, "question_id");
        if (version(args.base_version) !== 0) throw new ToolError({ error: "board_version_conflict" });
        const q = question(args);
        const principal = sponsor(actor);
        return withEnvelope(c, actor, "ask-board-question", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const rows = await c.query(
            `insert into board_question (organization_tenant_id,sponsoring_human_slug,board_id,question_id,revision,current,
               prompt,choices,allow_free_text,default_answer,asker_ref,asked_by_actor_id)
             values ($1,$2,$3,$4,1,true,$5,$6::jsonb,$7,$8,$9,$10)
             on conflict do nothing returning *`,
            [tenant, principal, board, id, q.prompt, JSON.stringify(q.choices), q.allow_free_text,
             q.default_answer, q.asker_ref, actor.id]);
          if (!rows.rows.length) throw new ToolError({ error: "board_version_conflict" });
          await writeEvent(c, actor, "ask-board-question", "board_question", rows.rows[0].id,
            { new: { board_id: board, question_id: id, revision: 1 }, idempotency_key: args.idempotency_key });
          return { ok: true, question: rows.rows[0] };
        });
      },
    },

    "revise-board-question": {
      write: true,
      description: "Revise an existing question by its current revision. Prior revisions and answers remain in the record.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, board_id: { type: "string" },
        question_id: { type: "string" }, base_version: { type: "integer" }, ...questionFields,
      }, required: ["idempotency_key", "board_id", "question_id", "base_version", "prompt", "asker_ref"] },
      handler: async (c, actor, args) => {
        const board = ref(args.board_id, "board_id");
        const id = ref(args.question_id, "question_id");
        version(args.base_version, 1);
        const q = question(args);
        const principal = sponsor(actor);
        return withEnvelope(c, actor, "revise-board-question", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const old = await c.query(
            `update board_question set current=false,updated_at=now()
              where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id=$3 and question_id=$4
                and revision=$5 and current=true returning revision`,
            [tenant, principal, board, id, args.base_version]);
          if (!old.rows.length) throw new ToolError({ error: "board_version_conflict" });
          const rows = await c.query(
            `insert into board_question (organization_tenant_id,sponsoring_human_slug,board_id,question_id,revision,current,
               prompt,choices,allow_free_text,default_answer,asker_ref,asked_by_actor_id)
             values ($1,$2,$3,$4,$5,true,$6,$7::jsonb,$8,$9,$10,$11) returning *`,
            [tenant, principal, board, id, args.base_version + 1, q.prompt, JSON.stringify(q.choices),
             q.allow_free_text, q.default_answer, q.asker_ref, actor.id]);
          await writeEvent(c, actor, "revise-board-question", "board_question", rows.rows[0].id,
            { new: { board_id: board, question_id: id, revision: args.base_version + 1 },
              idempotency_key: args.idempotency_key });
          return { ok: true, question: rows.rows[0] };
        });
      },
    },

    "list-progress-boards": {
      write: false,
      description: "List published progress boards visible to the authenticated tenant and sponsor, with publication time and task counts by status. Returns progress-board-directory.v1; snapshots and questions are read separately.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c, actor) => {
        const tenant = organizationTenantForActor(actor), principal = sponsor(actor);
        const result = await c.query(
          `select board_id,snapshot_json,updated_at from board_snapshot
            where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id <> $3
            order by case when board_id='carr-v5' then 0 else 1 end,board_id`,
          [tenant, principal, NEEDS_JOE_LOCAL_BOARD]);
        return { ok: true, schema: "progress-board-directory.v1", boards: result.rows.map(progressBoardSummary) };
      },
    },

    "read-progress-board": {
      write: false,
      description: "Read the published board and its current questions with durable Sent, Received and Applied answer state.",
      inputSchema: { type: "object", properties: { board_id: { type: "string" } }, required: ["board_id"] },
      handler: async (c, actor, args) => {
        const tenant = organizationTenantForActor(actor), principal = sponsor(actor);
        const board = ref(args.board_id, "board_id");
        const snapshot = await c.query(
          `select board_id,version,snapshot_json,updated_at from board_snapshot
            where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id=$3`,
          [tenant, principal, board]);
        const questions = await c.query(
          `select q.question_id,q.revision,q.prompt,q.choices,q.allow_free_text,q.default_answer,
                  q.asker_ref,q.asked_at,a.id as answer_id,a.answer_text,a.answered_by,
                  a.sent_at,a.received_at,a.applied_at,a.effect_ref,a.version as answer_version,
                  ${statusSql("a")} as status
             from board_question q left join board_answer a
               on a.organization_tenant_id=q.organization_tenant_id
              and a.sponsoring_human_slug=q.sponsoring_human_slug and a.board_id=q.board_id
              and a.question_id=q.question_id and a.question_revision=q.revision
            where q.organization_tenant_id=$1 and q.sponsoring_human_slug=$2 and q.board_id=$3 and q.current=true
            order by q.asked_at,q.question_id`, [tenant, principal, board]);
        return { ok: true, snapshot: snapshot.rows[0] ?? null, questions: questions.rows };
      },
    },

    "answer-board-question": {
      write: true, humanOnly: true,
      description: "Partner answers the current revision. Sent means this answer row was durably written; answered_by comes from authenticated identity.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, board_id: { type: "string" },
        question_id: { type: "string" }, base_version: { type: "integer" }, answer_text: { type: "string" },
      }, required: ["idempotency_key", "board_id", "question_id", "base_version", "answer_text"] },
      handler: async (c, actor, args) => {
        const partner = actor.human === true ? partnerAuthoritySlugForActor(actor) : null;
        if (!partner) throw new ToolError({ error: "board_answer_requires_human_partner" });
        const board = ref(args.board_id, "board_id"), id = ref(args.question_id, "question_id");
        version(args.base_version, 1);
        const answer = text(args.answer_text, "answer_text");
        return withEnvelope(c, actor, "answer-board-question", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const found = await c.query(
            `select question_id,revision,choices,allow_free_text,default_answer,asker_ref
               from board_question where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id=$3
                 and question_id=$4 and revision=$5 and current=true for update`,
            [tenant, partner, board, id, args.base_version]);
          if (!found.rows.length) throw new ToolError({ error: "board_version_conflict" });
          const q = found.rows[0];
          const choices = Array.isArray(q.choices) ? q.choices : JSON.parse(q.choices);
          if (!q.allow_free_text && !choices.includes(answer))
            throw new ToolError({ error: "board_answer_choice_invalid" });
          const rows = await c.query(
            `insert into board_answer (organization_tenant_id,sponsoring_human_slug,board_id,question_id,question_revision,
               asker_ref,answer_text,answered_by,answered_by_actor_id,default_overridden)
             values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
             on conflict do nothing returning *, 'Sent' as status`,
            [tenant, partner, board, id, args.base_version, q.asker_ref,
             answer, partner, actor.id, q.default_answer !== null && answer !== q.default_answer]);
          if (!rows.rows.length) throw new ToolError({ error: "board_answer_already_sent" });
          await writeEvent(c, actor, "answer-board-question", "board_answer", rows.rows[0].id,
            { new: { question_id: id, revision: args.base_version, answered_by: partner },
              idempotency_key: args.idempotency_key });
          return { ok: true, answer: rows.rows[0] };
        });
      },
    },

    "read-board-answers": {
      write: false,
      description: "Read new answers for the authenticated sponsor and one asker reference after a numeric cursor; reading alone does not mark Received.",
      inputSchema: { type: "object", properties: {
        after_cursor: { type: "integer" }, asker_ref: { type: "string" }, limit: { type: "integer" },
      }, required: ["after_cursor", "asker_ref"] },
      handler: async (c, actor, args) => {
        if (!Number.isSafeInteger(args.after_cursor) || args.after_cursor < 0)
          throw new ToolError({ error: "board_cursor_invalid" });
        const asker = ref(args.asker_ref, "asker_ref");
        const principal = sponsor(actor);
        const limit = args.limit ?? 100;
        if (!Number.isInteger(limit) || limit < 1 || limit > 500)
          throw new ToolError({ error: "board_limit_invalid" });
        const rows = await c.query(
          `select a.cursor,a.id,a.board_id,a.question_id,a.question_revision,a.asker_ref,
                  a.answer_text,a.answered_by,a.sent_at,a.received_at,a.applied_at,
                  a.effect_ref,a.version,a.default_overridden,${statusSql("a")} as status
             from board_answer a where a.organization_tenant_id=$1
               and a.sponsoring_human_slug=$2 and a.asker_ref=$3
               and a.cursor > $4 order by a.cursor limit $5`,
          [organizationTenantForActor(actor), principal, asker, args.after_cursor, limit]);
        return { ok: true, answers: rows.rows,
          next_cursor: rows.rows.length ? rows.rows.at(-1).cursor : args.after_cursor };
      },
    },

    "acknowledge-board-answer": {
      write: true,
      description: "Mark a sponsor-owned answer Received after the named asker or orchestrator has stored it in its inbox. Authenticated actor is recorded separately from the asker reference.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, answer_id: { type: "string" },
        asker_ref: { type: "string" }, base_version: { type: "integer" },
      }, required: ["idempotency_key", "answer_id", "asker_ref", "base_version"] },
      handler: async (c, actor, args) => {
        answerId(args.answer_id); ref(args.asker_ref, "asker_ref"); version(args.base_version, 1);
        const principal = sponsor(actor);
        return withEnvelope(c, actor, "acknowledge-board-answer", args, async () => {
          const rows = await c.query(
            `update board_answer a set received_at=now(),received_by_actor_id=$1,
                 received_for_ref=$2,version=version+1
               where a.organization_tenant_id=$3 and a.sponsoring_human_slug=$4
                 and a.id=$5 and a.asker_ref=$2 and a.version=$6 and a.received_at is null
               returning *, 'Received' as status`,
            [actor.id, args.asker_ref, organizationTenantForActor(actor), principal,
             args.answer_id, args.base_version]);
          if (!rows.rows.length) throw new ToolError({ error: "board_version_conflict" });
          await writeEvent(c, actor, "acknowledge-board-answer", "board_answer", args.answer_id,
            { new: { status: "Received", received_for: args.asker_ref },
              idempotency_key: args.idempotency_key });
          return { ok: true, answer: rows.rows[0] };
        });
      },
    },

    "record-board-answer-applied": {
      write: true,
      description: "Mark a Received answer Applied only with a concrete effect reference. The record names who applied it.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, answer_id: { type: "string" },
        base_version: { type: "integer" }, effect_ref: { type: "string" },
      }, required: ["idempotency_key", "answer_id", "base_version", "effect_ref"] },
      handler: async (c, actor, args) => {
        answerId(args.answer_id); version(args.base_version, 2);
        const effect = text(args.effect_ref, "effect_ref", 1000);
        const principal = sponsor(actor);
        return withEnvelope(c, actor, "record-board-answer-applied", args, async () => {
          const rows = await c.query(
            `update board_answer a set applied_at=now(),applied_by_actor_id=$1,
                 effect_ref=$2,version=version+1
               where a.organization_tenant_id=$3 and a.sponsoring_human_slug=$4
                 and a.id=$5 and a.version=$6
                 and a.received_at is not null and a.applied_at is null
               returning *, 'Applied' as status`,
            [actor.id, effect, organizationTenantForActor(actor), principal,
             args.answer_id, args.base_version]);
          if (!rows.rows.length) throw new ToolError({ error: "board_version_conflict" });
          await writeEvent(c, actor, "record-board-answer-applied", "board_answer", args.answer_id,
            { new: { status: "Applied", effect_ref: effect }, idempotency_key: args.idempotency_key });
          return { ok: true, answer: rows.rows[0] };
        });
      },
    },
  };
}
