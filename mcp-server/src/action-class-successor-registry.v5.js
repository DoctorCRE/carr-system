// DoctorCRE v5 slice V5-D01: inactive action-specific autonomy successors
// (doctrine `doctorcre-v5-astra-integration-review`, section
// v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09,
// proposed_id V5-D01). Migration 0708 installs the append-only
// action_class_successor table, its read function and its gate function; this
// module is the only door onto them.
//
// THREE VERBS, AND NO OTHERS:
//   register-action-class-successor   write   append one typed, inactive entry
//                                              (humanOnly: a partner act)
//   read-action-class-successors      read    list registered entries
//   read-action-class-gate            read    the deterministic activation gate
//
// WHAT THIS SLICE IS NOT. There is no activate verb, no update verb, no delete
// verb. registration cannot mint an effect capability: the migration's own
// CHECK constraint admits only status='inactive', so nothing this module does
// can ever produce a row this gate reads as allowed. The gate itself is
// unconditional as shipped -- every action_class it is asked about, registered
// or not, comes back denied. That is the "future gate": its shape exists (a
// per-action-class check any later unattended-action door would call before
// acting) but its answer today is always no. A denial names only the one
// action_class asked about; it reads the registry and returns, touching no
// other table, no other verb, and no other action_class's row.

const ACTION_CLASS = /^[a-z][a-z0-9_]{2,63}$/;

function refuse(ToolError, error, detail) {
  throw new ToolError({ error, ...detail });
}

function assertPlainObject(ToolError, value, path) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    refuse(ToolError, "invalid_shape", { message: `${path} must be an object`, path });
}

export function actionClassSuccessorRegistryTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    // HUMAN-ONLY (partner authority), fixed after independent review of PR
    // #1290. A registration is unique, append-only and immutable, and it
    // fixes the accountable owner and the activation_predicate every later
    // activation door would have to meet -- forever. Letting any
    // authenticated agent make that first, permanent choice would hand the
    // bar for future unattended action to whoever called first. So it is a
    // partner act: the dispatcher's humanOnly gate (tools.js) admits only the
    // verified partner or a server-verified native agent bound to that
    // partner's sponsor-scoped authority connection, and refuses every other
    // principal before schema validation or database access. The two reads
    // stay open: reading the registry or the (always-deny) gate grants nothing.
    "register-action-class-successor": {
      write: true, humanOnly: true,
      description: "HUMAN-ONLY (partner authority). Append ONE typed, inactive successor entry for a future unattended action class (e.g. salesforce_unattended_write, email_unattended_send): its owner, policy/data/model requirements and the activation predicate a later, separate human-gated door would have to satisfy. Grants no authority and activates nothing -- the stored row's status is permanently 'inactive' (the migration's CHECK constraint admits no other value) and no provider data or credential is accepted here. Refused if the action_class is already registered; this door never edits or reactivates an existing entry.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          action_class: { type: "string", description: "stable slug, e.g. salesforce_unattended_write; lowercase, 3-64 chars, [a-z0-9_], must start with a letter" },
          title: { type: "string" },
          goal: { type: "string" },
          owner: { type: "string", description: "who is accountable for this future action class, e.g. a partner slug or role name" },
          policy_requirements: { type: "object", description: "structured, no free-text credentials or secrets" },
          data_requirements: { type: "object" },
          model_requirements: { type: "object" },
          activation_predicate: { type: "object", description: "what would have to become true before a future activation door could even consider this class. Never evaluated here." },
        },
        required: ["idempotency_key", "action_class", "title", "goal", "owner", "activation_predicate"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "register-action-class-successor", args, async () => {
        const actionClass = String(args.action_class || "");
        if (!ACTION_CLASS.test(actionClass))
          refuse(ToolError, "action_class_invalid", { message: "action_class must match ^[a-z][a-z0-9_]{2,63}$", action_class: actionClass });
        const title = (args.title || "").trim();
        if (!title) refuse(ToolError, "title_required", {});
        const goal = (args.goal || "").trim();
        if (!goal) refuse(ToolError, "goal_required", {});
        const owner = (args.owner || "").trim();
        if (!owner) refuse(ToolError, "owner_required", {});
        const policyRequirements = args.policy_requirements ?? {};
        const dataRequirements = args.data_requirements ?? {};
        const modelRequirements = args.model_requirements ?? {};
        assertPlainObject(ToolError, policyRequirements, "policy_requirements");
        assertPlainObject(ToolError, dataRequirements, "data_requirements");
        assertPlainObject(ToolError, modelRequirements, "model_requirements");
        assertPlainObject(ToolError, args.activation_predicate, "activation_predicate");

        const existing = await c.query(
          "select 1 from action_class_successor where action_class=$1", [actionClass]);
        if (existing.rows.length)
          refuse(ToolError, "action_class_already_registered", { action_class: actionClass,
            hint: "this door appends new classes only; it does not edit an existing entry" });

        const row = (await c.query(
          `insert into action_class_successor
             (action_class, title, goal, owner, policy_requirements, data_requirements,
              model_requirements, activation_predicate, actor_id, idempotency_key)
           values ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10)
           returning id, status, created_at`,
          [actionClass, title, goal, owner, JSON.stringify(policyRequirements),
           JSON.stringify(dataRequirements), JSON.stringify(modelRequirements),
           JSON.stringify(args.activation_predicate), actor.id, args.idempotency_key],
        )).rows[0];

        await writeEvent(c, actor, "register-action-class-successor", "action_class_successor", row.id,
          { action_class: actionClass, title, goal, owner, status: row.status,
            idempotency_key: args.idempotency_key });

        return { ok: true, id: row.id, action_class: actionClass, status: row.status,
                 created_at: row.created_at, capability_issued: false };
      }),
    },

    "read-action-class-successors": {
      write: false,
      description: "List registered action-class successor entries (V5-D01), optionally filtered to one action_class. Every entry returned has status 'inactive' -- there is no other value this store can hold.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { action_class: { type: "string" } },
      },
      handler: async (c, _actor, args) => {
        const actionClass = args?.action_class !== undefined ? String(args.action_class) : null;
        if (actionClass !== null && !ACTION_CLASS.test(actionClass))
          refuse(ToolError, "action_class_invalid", { message: "action_class must match ^[a-z][a-z0-9_]{2,63}$" });
        const rows = (await c.query(
          "select * from read_action_class_successors($1::text)", [actionClass])).rows;
        return { ok: true, entries: rows.map(r => ({
          id: r.id, action_class: r.action_class, title: r.title, goal: r.goal, owner: r.owner,
          policy_requirements: r.policy_requirements, data_requirements: r.data_requirements,
          model_requirements: r.model_requirements, activation_predicate: r.activation_predicate,
          status: r.status, actor: r.actor, created_at: r.created_at,
        })) };
      },
    },

    "read-action-class-gate": {
      write: false,
      description: "The deterministic future-action gate (V5-D01) for ONE action_class: whether an unattended action of that class is currently allowed. As shipped this always answers allowed:false -- registered-but-inactive or unregistered, both deny -- because no activation door exists yet. A denial is scoped to the one action_class asked about and has no effect on any other action_class or verb.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { action_class: { type: "string" } },
        required: ["action_class"],
      },
      handler: async (c, _actor, args) => {
        const actionClass = String(args.action_class || "");
        if (!ACTION_CLASS.test(actionClass))
          refuse(ToolError, "action_class_invalid", { message: "action_class must match ^[a-z][a-z0-9_]{2,63}$" });
        const row = (await c.query(
          "select * from action_class_successor_gate($1::text)", [actionClass])).rows[0];
        return { ok: true, action_class: row.action_class, allowed: row.allowed,
                 reason: row.reason, registered: row.registered };
      },
    },
  };
}
