// Contact evidence is captured from the local mail/calendar stores. This job
// consumes those dated activities; it never opens a mailbox or sends a message.
import { createHash } from "node:crypto";
export const LEAD_AUTOMATION_CONTRACT = "lead-automation.v1";
const bodyDigest = body => createHash("sha256").update(body).digest("hex");
const ACTIVE = ["new", "qualified", "outreach_active", "engaged"];
const ARCHIVABLE = [...ACTIVE, "nurture_drip", "opportunity"];
const ARCHIVE_REASONS = { retired: "Retired", sold_to_platform: "Practice sold to a platform", another_broker: "Signed with another broker" };
export function moveReason(activity, to) {
  const m = metadata(activity);
  const date = new Date(activity.occurred_at).toISOString().slice(0, 10);
  const fact = to === "archived" ? ARCHIVE_REASONS[m.archive_reason] :
    activity.kind === "email_in" ? "Reply received" : activity.kind === "email_out" ? "Approved first contact sent" :
    activity.kind === "tour" ? "Tour held" : activity.kind === "call" ? "Call held" : "Meeting held";
  return `${fact} ${date}`;
}
const MAIL = new Set(["mail_ingest", "local_mail"]);
const CALENDAR = new Set(["calendar", "calendar_ingest"]);

export function nextMorning(now, timeZone) {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  const parts = date => Object.fromEntries(fmt.formatToParts(date).map(p => [p.type, p.value]));
  const local = parts(new Date(now));
  const target = Date.UTC(+local.year, +local.month - 1, +local.day + 1, 6);
  let candidate = target;
  for (let i = 0; i < 4; i++) {
    const p = parts(new Date(candidate));
    const rendered = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
    candidate += target - rendered;
  }
  return new Date(candidate).toISOString();
}

function metadata(activity) {
  try {
    const value = JSON.parse(activity.detail || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

// Questions, in order: is the lead live and unsuppressed? Is there a dated
// activity bound to this lead? Did the local reader establish an exact match?
// Does its factual kind support the NEXT stage? Unstructured signals stay proposals.
export function planLeadMoves(leads, activities, drafts, now) {
  const clock = new Date(now).getTime();
  if (!Number.isFinite(clock)) throw new TypeError("invalid_clock");
  return leads.flatMap(lead => {
    if (lead.suppressed || lead.party_merged || lead.party_suppressed || !ARCHIVABLE.includes(lead.stage)) return [];
    const evidence = activities.filter(a => a.lead_id === lead.id &&
      Number.isFinite(new Date(a.occurred_at).getTime()) && new Date(a.occurred_at).getTime() <= clock &&
      new Date(a.occurred_at).getTime() >= new Date(lead.created_at).getTime())
      .sort((a, b) => Number(metadata(b).lead_stage_signal==="archived")-Number(metadata(a).lead_stage_signal==="archived") || new Date(a.occurred_at) - new Date(b.occurred_at) || a.id.localeCompare(b.id));
    let proposal = null;
    for (const a of evidence) {
      const m = metadata(a);
      const exact = m.match === "exact" && m.party_id === lead.party_id &&
        typeof m.evidence_ref === "string" && m.evidence_ref.length > 0 && !a.owed;
      const mail = MAIL.has(a.source);
      const meeting = CALENDAR.has(a.source) && ["meeting", "call", "tour"].includes(a.kind) &&
        m.attended === true && typeof m.ended_at === "string" && Number.isFinite(new Date(m.ended_at).getTime()) && new Date(m.ended_at).getTime() >= new Date(a.occurred_at).getTime() && new Date(m.ended_at).getTime() <= clock;
      const reply = mail && a.kind === "email_in" && m.automated === false;
      let to = null, strong = false;
      if (m.lead_stage_signal === "archived") {
        if (!Object.hasOwn(ARCHIVE_REASONS,m.archive_reason) || !(reply || meeting)) continue;
        to = "archived"; // Evidence can propose a permanent exit; only a partner applies it.
      } else if (lead.stage === "new" && (reply || meeting)) {
        to = "qualified";
        strong = exact && lead.event_confidence === "high" && Number.isFinite(Date.parse(lead.est_lease_event || ""));
      } else if (lead.stage === "qualified" && mail && a.kind === "email_out") {
        const draft = drafts.find(d => d.lead_id === lead.id && d.id === m.first_contact_draft_id &&
          d.approved_at && d.party_id === lead.party_id &&
          new Date(a.occurred_at).getTime() >= new Date(d.approved_at).getTime() &&
          new Date(a.occurred_at).getTime() >= new Date(d.scheduled_for).getTime());
        if (draft) { to = "outreach_active"; strong = exact && typeof draft.body === "string" && m.draft_body_sha256 === bodyDigest(draft.body); }
      } else if (lead.stage === "outreach_active" && (reply || meeting) &&
        new Date(a.occurred_at).getTime() > new Date(lead.stage_since).getTime()) {
        to = "engaged"; strong = exact;
      } else if (lead.stage === "engaged" && ["nurture_drip", "opportunity"].includes(m.lead_stage_signal) &&
        (mail || CALENDAR.has(a.source)) && new Date(a.occurred_at).getTime() > new Date(lead.stage_since).getTime()) {
        to = m.lead_stage_signal;
        // Silence and unstructured interpretations stay weak. A dated inbound
        // deferral or a held tour supplies a factual boundary for the move.
        strong = exact && ((to === "nurture_drip" && reply &&
          /^\d{4}-\d{2}-\d{2}$/.test(m.follow_up_after || "") && Date.parse(m.follow_up_after) > clock && new Date(m.follow_up_after).toISOString().slice(0,10) === m.follow_up_after) ||
          (to === "opportunity" && meeting && a.kind === "tour" && lead.event_confidence === "high" && Number.isFinite(Date.parse(lead.est_lease_event || ""))));
      }
      if (to) {
        const move = { lead_id: lead.id, party_id: lead.party_id, from_stage: lead.stage, to_stage: to,
        base_version: lead.version, activity_id: a.id, reason: moveReason(a, to),
        evidence_ref: m.evidence_ref || `activity:${a.id}`, strength: strong ? "strong" : "weak",
        status: strong ? "applied" : "proposed" };
        if (to === "archived" || strong) return [move];
        proposal ??= move;
      }
    }
    return proposal ? [proposal] : [];
  });
}

const LEADS_SQL = `select l.*, (p.merged_into is not null or p.deleted_at is not null) as party_merged,
  (p.contact_state <> 'active') as party_suppressed,
  coalesce((select case when e.cause='automation_job' then coalesce(a.occurred_at,e.recorded_at) else e.recorded_at end
    from event e left join activity a on a.id::text=e.new_value->>'activity_id' and a.lead_id=l.id
    where e.subject_type='lead' and e.subject_id=l.id and e.field='stage' and e.new_value->>'stage'=l.stage
    order by e.recorded_at desc,e.id desc limit 1),l.created_at) as stage_since
  from lead l join party p on p.id=l.party_id order by l.id`;
const ACTIVITIES_SQL = `select a.* from activity a join lead l on l.id=a.lead_id
  where a.source in ('mail_ingest','local_mail','calendar','calendar_ingest')
    and not exists(select 1 from lead_stage_move m where m.lead_id=l.id
      and m.from_stage=l.stage and m.activity_id=a.id and m.status in ('applied','undone'))
  order by a.occurred_at,a.id`;
const DRAFTS_SQL = `select * from lead_contact_draft order by created_at,id`;

export function leadAutomationTools({ withEnvelope, writeEvent, ToolError, invoices }) {
  const fail = error => { throw new ToolError({ error }); };
  const schema = properties => ({ type: "object", additionalProperties: false, properties });
  async function snapshot(c, lock = false) {
    const now = (await c.query("select now() as now")).rows[0].now;
    const leads = (await c.query(LEADS_SQL + (lock ? " for update of l,p" : ""))).rows;
    const activities = (await c.query(ACTIVITIES_SQL)).rows;
    const drafts = (await c.query(DRAFTS_SQL)).rows;
    return { now, leads, activities, drafts };
  }
  async function apply(c, actor, args) {
    // Lock the live rows before observing evidence; a competing human edit must
    // be seen here, not overwritten by a planner's stale snapshot.
    const s = await snapshot(c, true);
    const moves = [];
    for (const move of planLeadMoves(s.leads, s.activities, s.drafts, s.now)) {
      await c.query("savepoint lead_stage_effect");
      const row = (await c.query(`insert into lead_stage_move
        (lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,created_by,reason)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9) on conflict (lead_id,from_stage,to_stage,activity_id) do update
        set strength=excluded.strength,reason=excluded.reason
        where lead_stage_move.status='proposed' returning id`,
      [move.lead_id, move.from_stage, move.to_stage, move.activity_id, move.evidence_ref, move.strength, "proposed", actor.id, move.reason])).rows[0];
      if (!row) { await c.query("release savepoint lead_stage_effect"); continue; }
      if (move.status === "proposed") {
        moves.push({ ...move, move_id: row.id });
        await c.query("release savepoint lead_stage_effect");
        continue;
      }
      const updated = await c.query("update lead set stage=$1,updated_by=$2 where id=$3 and version=$4 and stage=$5 and not suppressed returning id",
        [move.to_stage,actor.id,move.lead_id,move.base_version,move.from_stage]);
      if (updated.rowCount !== 1) {
        await c.query("rollback to savepoint lead_stage_effect");
        await c.query("release savepoint lead_stage_effect");
        continue;
      }
      await c.query("update lead_stage_move set status='applied',strength='strong' where id=$1", [row.id]);
      await writeEvent(c, actor, "advance-leads", "lead", move.lead_id,
        { recorded_at_after_lock: true, field: "stage", old: { stage: move.from_stage }, new: { stage: move.to_stage, evidence_ref: move.evidence_ref, activity_id: move.activity_id, move_id: row.id, reason: move.reason }, cause: "automation_job" });
      moves.push({ ...move, move_id: row.id });
      await c.query("release savepoint lead_stage_effect");
    }
    // Includes human-qualified rows and heals an interrupted draft preparation.
    // A previous first contact suppresses a duplicate introduction.
    const qualified = (await c.query(`select l.id,l.party_id,l.owner_id,p.name
      from lead l join party p on p.id=l.party_id
      where l.stage='qualified' and not l.suppressed and p.merged_into is null and p.deleted_at is null and p.contact_state='active'
        and not exists(select 1 from activity a where a.lead_id=l.id and a.kind='email_out')
        and not exists(select 1 from lead_contact_draft d where d.lead_id=l.id)
      order by l.id for update of l,p`)).rows;
    let draftsPrepared = 0;
    for (const l of qualified) {
      const timezone = args.time_zone || "America/Chicago";
      const schedule = nextMorning(s.now, timezone);
      const prepared = await c.query(`insert into lead_contact_draft
        (lead_id,party_id,owner_id,subject,body,scheduled_for,time_zone,created_by)
        values ($1,$2,$3,$4,$5,$6,$7,$8) on conflict (lead_id) do nothing returning id`,
      [l.id,l.party_id,l.owner_id,"Your next practice space",
        "I help healthcare practices plan their next space, representing tenants and buyers. If a move or renewal is on your horizon, would a brief conversation be useful?",
        schedule,timezone,actor.id]);
      draftsPrepared += prepared.rowCount;
    }
    const invoice_closes = await invoices.apply(c, actor, args);
    return { ok: true, contract: LEAD_AUTOMATION_CONTRACT, moves, invoice_closes, drafts_prepared: draftsPrepared, sent: false };
  }
  return {
    "record-lead-contact": {
      write: true, description: "Record derived contact evidence from a local mail or calendar reader. Match the counterparty address against the lead's party; a domain-only match remains weak. No source body or credentials accepted. Never sends.",
      inputSchema: { ...schema({ idempotency_key:{type:"string"},lead:{type:"string"},native_ref:{type:"string",minLength:1,maxLength:500},
        counterparty_address:{type:"string"},kind:{type:"string",enum:["email_in","email_out","meeting","call","tour"]},
        occurred_at:{type:"string",format:"date-time"},ended_at:{type:"string",format:"date-time"},
        attended:{type:"boolean"},automated:{type:"boolean"},first_contact_draft_id:{type:"string",format:"uuid"},
        draft_body_sha256:{type:"string",pattern:"^[0-9a-f]{64}$"},
        follow_up_after:{type:"string",format:"date"},lead_stage_signal:{type:"string",enum:["nurture_drip","opportunity","archived"]},archive_reason:{type:"string",enum:Object.keys(ARCHIVE_REASONS)} }),
        required:["idempotency_key","lead","native_ref","counterparty_address","kind","occurred_at"] },
      handler:(c,actor,args) => withEnvelope(c,actor,"record-lead-contact",args,async () => {
        if ((args.lead_stage_signal === "archived") !== !!args.archive_reason) fail("archive_reason_required");
        const when = new Date(args.occurred_at).getTime();
        const now = new Date((await c.query("select now() as now")).rows[0].now).getTime();
        if (!Number.isFinite(when) || when>now || !args.native_ref?.trim() || args.native_ref.length>500) fail("invalid_contact_evidence");
        const mail=["email_in","email_out"].includes(args.kind);
        if (!(mail ? /^local-mail:/ : /^local-calendar:/).test(args.native_ref)) fail("local_evidence_reference_required");
        if (!mail && (!args.attended || typeof args.ended_at!=="string" || !Number.isFinite(Date.parse(args.ended_at)) ||
          Date.parse(args.ended_at)<when || Date.parse(args.ended_at)>now)) fail("held_calendar_event_required");
        const l = (await c.query(`select l.id,l.party_id,p.email from lead l join party p on p.id=l.party_id
          where l.registry_ref=$1 and p.merged_into is null and p.deleted_at is null and p.contact_state='active'`,[args.lead])).rows[0];
        if (!l) fail("lead_not_found");
        const address=String(args.counterparty_address||"").trim().toLowerCase();
        const known=String(l.email||"").trim().toLowerCase();
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(address)) fail("invalid_counterparty_address");
        const match=address===known ? "exact" : "unconfirmed";
        const detail={match,party_id:l.party_id,evidence_ref:args.native_ref,
          automated:args.automated ?? true,attended:args.attended ?? false,ended_at:args.ended_at,
          first_contact_draft_id:args.first_contact_draft_id,draft_body_sha256:args.draft_body_sha256,lead_stage_signal:args.lead_stage_signal,follow_up_after:args.follow_up_after,archive_reason:args.archive_reason};
        const a=(await c.query(`insert into activity
          (occurred_at,actor_id,kind,summary,detail,owed,lead_id,source)
          values ($1,$2,$3,$4,$5,$6,$7,$8) returning id`,
        [args.occurred_at,actor.id,args.kind,"Contact captured",JSON.stringify(detail),match==="exact"?null:"Confirm counterparty",l.id,mail?"local_mail":"calendar"])).rows[0];
        await writeEvent(c,actor,"record-lead-contact","lead",l.id,{new:{activity_id:a.id,evidence_ref:args.native_ref,match},cause:mail?"ingest_email":"ingest_calendar"});
        return {ok:true,activity_id:a.id,match,evidence_ref:args.native_ref};
      }),
    },
    "lead-stage-preview": {
      write: false, description: "Dry run: list evidence-backed lead moves and weak proposals without changing business records or preparing drafts.",
      inputSchema: schema({}), handler: async c => {
        const s = await snapshot(c);
        return { contract: LEAD_AUTOMATION_CONTRACT, dry_run: true, moves: planLeadMoves(s.leads,s.activities,s.drafts,s.now), invoice_closes: await invoices.preview(c) };
      },
    },
    "advance-leads": {
      write: true, description: "Run the lead stage job over captured local mail and calendar activities. Strong evidence advances one step with provenance; weak evidence becomes a proposal. Prepare approval-only first-contact drafts for 6 am local tomorrow. Never sends. dry_run performs the same planning without business writes.",
      inputSchema: { ...schema({ idempotency_key: { type: "string" }, dry_run: { type: "boolean" }, time_zone: { type: "string" } }), required: ["idempotency_key"] },
      handler: async (c, actor, args) => {
        nextMorning(new Date(),args.time_zone || "America/Chicago"); // Validate before writing.
        if (args.dry_run) {
          const s = await snapshot(c);
          return { contract: LEAD_AUTOMATION_CONTRACT, dry_run: true, moves: planLeadMoves(s.leads,s.activities,s.drafts,s.now), invoice_closes: await invoices.preview(c), sent: false };
        }
        return withEnvelope(c,actor,"advance-leads",args,() => apply(c,actor,args));
      },
    },
    "lead-approval-queue": {
      write: false, description: "First-contact drafts and proposed stage moves, with exact approval identifiers. Approval records a decision; sending remains in the partner's mail client.",
      inputSchema: schema({}), handler: async c => ({ contract: LEAD_AUTOMATION_CONTRACT,
        drafts: (await c.query(`select d.*,l.registry_ref,p.name from lead_contact_draft d
          join lead l on l.id=d.lead_id join party p on p.id=d.party_id
          where l.stage='qualified' and not l.suppressed and p.merged_into is null and p.deleted_at is null and p.contact_state='active' order by d.scheduled_for,d.id`)).rows,
        moves: (await c.query(`select m.*,l.version as base_version,l.registry_ref,p.name from lead_stage_move m
          join lead l on l.id=m.lead_id join party p on p.id=l.party_id
          where m.status='proposed' and m.from_stage=l.stage and not l.suppressed and p.merged_into is null and p.deleted_at is null and p.contact_state='active' order by m.created_at,m.id`)).rows }),
    },
    "approve-lead-draft": {
      write: true, humanOnly: true, description: "One-tap approval of the displayed first-contact draft. It remains a draft; this verb sends nothing and does not move the lead.",
      inputSchema: { ...schema({ idempotency_key: { type: "string" }, draft_id: { type: "string", format: "uuid" } }), required: ["idempotency_key","draft_id"] },
      handler: (c,actor,args) => withEnvelope(c,actor,"approve-lead-draft",args,async () => {
        if (!actor.human) fail("human_approval_required");
        const d = (await c.query(`select d.* from lead_contact_draft d join lead l on l.id=d.lead_id
          join party p on p.id=l.party_id where d.id=$1 and d.party_id=l.party_id
          and l.stage='qualified' and not l.suppressed and p.merged_into is null and p.deleted_at is null and p.contact_state='active' for update of l,p,d`, [args.draft_id])).rows[0];
        if (!d || d.approved_at) fail("draft_not_pending");
        await c.query("update lead_contact_draft set approved_at=now(),approved_by=$2 where id=$1",[d.id,actor.id]);
        await writeEvent(c,actor,"approve-lead-draft","lead",d.lead_id,{ new: { draft_id: d.id, approved: true, sent: false } });
        return { ok: true, draft_id: d.id, approved: true, sent: false };
      }),
    },
    "approve-lead-move": {
      write: true,humanOnly: true,description: "Apply a displayed weak-evidence lead stage proposal after a human confirms it. Preserve its activity reference and refuse a stale proposal.",
      inputSchema: { ...schema({ idempotency_key: { type: "string" }, move_id: { type: "string",format: "uuid" }, base_version: { type: "integer" } }), required: ["idempotency_key","move_id","base_version"] },
      handler: (c,actor,args) => withEnvelope(c,actor,"approve-lead-move",args,async () => {
        if (!actor.human) fail("human_approval_required");
        const m = (await c.query(`select m.*,l.version,l.stage from lead_stage_move m
          join lead l on l.id=m.lead_id join party p on p.id=l.party_id
          where m.id=$1 and m.status='proposed' and not l.suppressed and p.merged_into is null and p.deleted_at is null and p.contact_state='active' for update of l,p,m`,[args.move_id])).rows[0];
        if (!m || m.version !== args.base_version || m.stage !== m.from_stage) fail("stale_lead_proposal");
        await c.query("update lead set stage=$1,updated_by=$2 where id=$3",[m.to_stage,actor.id,m.lead_id]);
        await c.query("update lead_stage_move set status='applied',approved_by=$2,approved_at=now() where id=$1",[m.id,actor.id]);
        await writeEvent(c,actor,"approve-lead-move","lead",m.lead_id,{ recorded_at_after_lock: true, field: "stage",old: {stage:m.from_stage},new: { stage:m.to_stage,evidence_ref:m.evidence_ref,activity_id:m.activity_id,move_id:m.id,reason:m.reason },cause:"human_stated" });
        return { ok:true,stage:m.to_stage,evidence_ref:m.evidence_ref };
      }),
    },
    "undo-lead-move": {
      write: true, humanOnly: true, description: "Undo the latest applied lead stage move. Restore its prior stage and record who undid it; refuse newer stage work or a stale version.",
      inputSchema: { ...schema({idempotency_key:{type:"string"},move_id:{type:"string",format:"uuid"},base_version:{type:"integer"}}), required:["idempotency_key","move_id","base_version"] },
      handler:(c,actor,args) => withEnvelope(c,actor,"undo-lead-move",args,async()=>{
        if (!actor.human) fail("human_approval_required");
        const m=(await c.query(`select m.*,l.stage,l.version from lead_stage_move m join lead l on l.id=m.lead_id
          where m.id=$1 for update of l,m`,[args.move_id])).rows[0];
        if (!m || m.status!=="applied" || m.stage!==m.to_stage || m.version!==args.base_version) fail("stale_lead_undo");
        const latest=(await c.query(`select id,new_value from event where subject_type='lead' and subject_id=$1 and field='stage'
          order by recorded_at desc,id desc limit 1`,[m.lead_id])).rows[0];
        let associated=latest?.new_value?.move_id===m.id;
        if(latest && !Object.hasOwn(latest.new_value||{},"move_id")) {
          // Delivered predecessor approvals had no move_id. Authenticate the
          // latest event against the unique persisted approval, without changing
          // history or accepting a later same-stage edit as that approval.
          associated=(await c.query(`select count(*)=1 and bool_and(m.id=$2) as associated
            from event e join lead_stage_move m on m.lead_id=e.subject_id
            join actor a on a.id=e.actor_id and a.kind='human'
            where e.id=$1 and e.verb='approve-lead-move' and e.cause='human_stated'
              and m.status='applied' and m.approved_by=e.actor_id
              and m.approved_at=e.occurred_at and e.recorded_at>=m.approved_at
              and e.old_value->>'stage'=m.from_stage and e.new_value->>'stage'=m.to_stage
              and e.new_value->>'activity_id'=m.activity_id::text
              and e.new_value->>'evidence_ref'=m.evidence_ref`,[latest.id,m.id])).rows[0].associated;
        }
        if(!associated) fail("newer_stage_change_exists");
        const changed=await c.query("update lead set stage=$1,updated_by=$2 where id=$3 returning id",[m.from_stage,actor.id,m.lead_id]);
        if (changed.rowCount!==1) fail("lead_undo_not_applied");
        await c.query("update lead_stage_move set status='undone',undone_at=now(),undone_by=$2 where id=$1",[m.id,actor.id]);
        await writeEvent(c,actor,"undo-lead-move","lead",m.lead_id,{recorded_at_after_lock:true,field:"stage",old:{stage:m.to_stage},new:{stage:m.from_stage,undone_move_id:m.id,undone_by:actor.id,reason:`Undid: ${m.reason}`,evidence_ref:m.evidence_ref},cause:"human_correction"});
        return {ok:true,stage:m.from_stage,undone_move_id:m.id,undone_by:actor.id};
      }),
    },
    "last-new-lead-search": {
      write: false,description: "Most recent completed new-lead search, with timestamp and outcome. A never-run search returns null; failure is an outcome, not a missing run.",
      inputSchema: schema({}),handler: async c => ({ contract:LEAD_AUTOMATION_CONTRACT,
        last_run:(await c.query(`select s.key as search_key,r.ended_at as timestamp,r.state as outcome,
          coalesce(r.evidence_ref,'run:'||r.id::text) as receipt_ref from ops.run r join ops.service s on s.id=r.service_id
          where s.key in ('lead-router','lead-sweep','renewal-radar','npi-sweep-weekly') and r.environment='production'
          and r.kind='job' and r.ended_at is not null and r.state in ('succeeded','failed','timed_out','cancelled','skipped')
          union all
          select j.definition_key as search_key,j.ended_at as timestamp,j.state as outcome,
          'job:'||j.id::text as receipt_ref from ops.job j
          where j.definition_key in ('lead-router','lead-sweep','renewal-radar-source-daily','npi-sweep-weekly')
          and j.mode='live' and j.ended_at is not null and j.state in ('succeeded','failed','timed_out','cancelled','skipped','dead_lettered')
          order by timestamp desc,receipt_ref desc limit 1`)).rows[0] || null }),
    },
  };
}
