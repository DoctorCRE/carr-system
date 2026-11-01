// Read lead guidance, quiet follow-up proposals, and sourced practice and market context.
import type { Db } from './verb-contract.ts';
import { ToolError } from './tool-error.ts';

const CONTACTABLE_STATES = ['active', 'nurture'];
const contactable = (state: string) => CONTACTABLE_STATES.includes(state);

type Lead = { id: string; stage: string; suppressed: boolean; contact_eligible?: boolean; contact_state?: string; next_action_date?: string | null;
  est_lease_event?: string | null; event_confidence?: string | null; lane?: string | null };
const DEAL_OBJECTIVE = 'Coordinate with the linked client and deal before creating competing follow-up.';
const CLOSED_OBJECTIVES: Record<string, string> = {
  closed_won: 'Closed won. Coordinate any post-close follow-up with the owning partner.',
  closed_lost: 'Closed lost. Review the recorded close with the owning partner before any future outreach.',
};
const OBJECTIVES: Record<string, string> = {
  new: 'Verify identity and discover whether the practice has a real estate decision.',
  qualified: 'Confirm the decision window, decision makers and an appropriate introduction.',
  outreach_active: 'Follow the agreed next step and offer one useful reason to respond.',
  engaged: 'Understand requirements and agree the next decision with the practice.',
  nurture_drip: 'Respect the requested timing and return with useful, relevant information.',
  opportunity: DEAL_OBJECTIVE,
  active_deal: DEAL_OBJECTIVE,
};
export function leadAdvice<T extends Lead>(lead: T) {
  const contact_eligible = lead.contact_state === undefined ? lead.contact_eligible :
    !lead.suppressed && lead.stage !== 'do_not_contact' && contactable(lead.contact_state);
  const closed = CLOSED_OBJECTIVES[lead.stage];
  const hold = lead.suppressed || contact_eligible === false || (!OBJECTIVES[lead.stage] && !closed);
  return { ...lead, contact_eligible,
    handling_advice: hold ? 'Do not contact. Review the recorded restriction or terminal stage with the owning partner.' : closed ||
      `${OBJECTIVES[lead.stage]} Research the handed record before preparing an outreach draft.`,
    strategy: { basis: 'stage_based_guidance', objective: hold ? 'Resolve the recorded hold before any outreach.' : closed || OBJECTIVES[lead.stage],
      discovery: ['What has the practice already tried?', 'What constraints and timing are firm?',
        'Who approves the decision?', 'What would make the practice stop?', 'What would success look like in 90 days?'],
      decision_window: lead.est_lease_event || null, confidence: lead.event_confidence || 'unrecorded',
      next_step: hold ? 'Ask the owning partner to review the hold.' : lead.next_action_date ?
        `Respect the recorded next action date ${lead.next_action_date}.` : 'Agree a next step and date; silence is not consent.',
      channel: 'Use a verified contact route. Keep outreach as a draft for a partner to send.',
      risk: 'Do not infer personal circumstances, intent, or a confirmed lease expiry from a sourcing signal.', sent: false },
  };
}

type Finding = { flag_id: string; kind: string; found: boolean; expired: boolean; proposes_correction: boolean;
  source: string; observed_at: string; expires_on: string | null; value: unknown };
export function researchSummary(evidence: Finding[] = []) {
  if (evidence.length > 500) throw new ToolError({ error: 'research_evidence_limit_exceeded' });
  const current = evidence.filter(e => !e.expired);
  const conflict = current.some(e => e.found && (e.kind === 'discrepancy' || e.proposes_correction));
  const verified = current.some(e => e.kind === 'verified' && e.found && e.source.trim());
  return { state: conflict ? 'needs_review' : verified ? 'verified' : 'needs_research', evidence,
    coverage: 'recorded_findings_only', external_search_performed: false,
    next_step: conflict ? 'Review discrepancies against original sources before working this record.' : verified ?
      'Read the cited sources and confirm the practice decision before drafting outreach.' :
      'Check practice website, professional registry and public filings; record sourced findings with record-finding, including nothing found.' };
}
export function researchEvidenceSql(partyId: string) {
  return `coalesce((select jsonb_agg(to_jsonb(f) order by f.observed_at desc,f.flag_id) from (
    select flag_id,kind,found,expired,proposes_correction,source,observed_at,expires_on,value
      from v_record_flag_subject f where (f.subject_type='party' and f.subject_id=${partyId})
        or exists(select 1 from v_ref_index r where r.party_id=${partyId}
          and r.subject_type=f.subject_type and r.subject_id=f.subject_id)
      order by observed_at desc,flag_id limit 501) f),'[]'::jsonb)`;
}
const marketFilters = { type: 'object', additionalProperties: false, properties: {
  vertical: { type: 'string', minLength: 1, maxLength: 200 }, city: { type: 'string', minLength: 1, maxLength: 200 },
  state: { type: 'string', pattern: '^[A-Z]{2}$' },
} };
type MarketFilter = { vertical?: string; city?: string; state?: string };

export function leadFollowUpTools() {
  return {
    'quiet-follow-ups': {
      write: false, writerConnection: true,
      description: 'Read-only follow-up proposals for quiet leads and active vendors. Suggested inactivity intervals default to 45 and 90 days and may be supplied explicitly. Uses the latest recorded contact date or creation date when never contacted. A lead next_action_date overrides the interval. Preserves every open task including future and held tasks; excludes suppressed/terminal leads, restricted parties, merged/deleted records, and leads linked to cold/paused clients. Includes source dates and owner. Never creates a task, changes a cadence rule, drafts or sends email. Existing cadence engine continues scheduled task succession.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        lead_days: { type: 'integer', minimum: 1, maximum: 365, default: 45 },
        vendor_days: { type: 'integer', minimum: 1, maximum: 365, default: 90 },
      } },
      handler: async (c: Db, _actor: unknown, args: { lead_days?: number; vendor_days?: number }) => {
        const cadence = { lead_days: args.lead_days ?? 45, vendor_days: args.vendor_days ?? 90 };
        const proposals = (await c.query(`with subjects as (
          select 'lead'::text subject_type,l.id subject_id,l.party_id,l.registry_ref ref,p.name,l.owner_id,
            coalesce(greatest(l.last_touch,t.last_touch::date),l.created_at::date) anchor_on,
            case when greatest(l.last_touch,t.last_touch::date) is null then 'created_at' else 'last_contact' end basis,
            l.next_action_date,cl.id client_id,$1::int interval_days
          from lead l join party p on p.id=l.party_id left join client cl on cl.id=l.client_id
            left join v_last_touch t on t.subject_type='lead' and t.subject_id=l.id
          where not l.suppressed and l.stage in ('new','qualified','outreach_active','engaged','nurture_drip')
            and p.merged_into is null and p.deleted_at is null and p.contact_state=any($3::text[])
            and (l.client_id is null or (cl.merged_into is null and coalesce(cl.status,'') not in ('cold','paused')))
          union all
          select 'vendor',v.id,v.party_id,v.vendor_ref,p.name,v.owner_id,
            coalesce(greatest(v.last_touch,t.last_touch::date),v.created_at::date),
            case when greatest(v.last_touch,t.last_touch::date) is null then 'created_at' else 'last_contact' end,
            null::date,null::uuid,$2::int
          from vendor v join party p on p.id=v.party_id
            left join v_last_touch t on t.subject_type='vendor' and t.subject_id=v.id
          where v.merged_into is null and v.disposition='active' and p.merged_into is null
            and p.deleted_at is null and p.contact_state=any($3::text[])), due as (
          select s.*,coalesce(next_action_date,anchor_on+interval_days) due_on
          from subjects s where not exists(select 1 from next_action n where n.status='open'
            and ((n.subject_type=s.subject_type and n.subject_id=s.subject_id)
              or (n.subject_type='client' and n.subject_id=s.client_id))))
          select d.*,a.slug owner,current_date-due_on days_overdue from due d
            left join actor a on a.id=d.owner_id where due_on<=current_date
          order by due_on,subject_type,subject_id limit 1001`, [cadence.lead_days,cadence.vendor_days,CONTACTABLE_STATES])).rows;
        if (proposals.length > 1000) throw new ToolError({ error: 'quiet_follow_up_limit_exceeded' });
        return { schema_version: 'quiet-follow-ups.v1', cadence, proposals, sent: false, tasks_created: false };
      },
    },
    'territory-state-board': {
      write: false,
      description: 'City/state submarket board for recorded practice geographies, with dated source evidence. City is the geographic grouping, not a verified submarket polygon. Reads record-finding kind=submarket_state with value fields city, state and condition=soft|balanced|tight. Current evidence requires found=true, an explicit unexpired expires_on and a nonfuture observation. Expired evidence remains visible. Missing evidence is unknown and conflicting current conditions stay conflicting. No condition is inferred from lead volume or a single negotiation. Exact optional city/state/vertical filters.',
      inputSchema: marketFilters,
      handler: async (c: Db, _actor: unknown, args: MarketFilter) => {
        const rows = (await c.query(`with markets as (
          select distinct p.city,p.state from party p where p.merged_into is null and p.deleted_at is null
            and p.city is not null and p.state is not null and p.specialty is not null
            and ($1::text is null or p.specialty=$1) and ($2::text is null or p.city=$2) and ($3::text is null or p.state=$3))
          select m.city,m.state,coalesce(e.items,'[]'::jsonb) evidence,
            case cardinality(e.conditions) when 1 then e.conditions[1] when 0 then 'unknown' else
              case when e.conditions is null then 'unknown' else 'conflicting' end end condition
          from markets m left join lateral (
            select jsonb_agg(jsonb_build_object('flag_id',f.flag_id,'condition',f.value->>'condition',
              'source',f.source,'observed_at',f.observed_at,'expires_on',f.expires_on,'expired',f.expired,
              'current',f.found and not f.expired and f.expires_on is not null and f.observed_at<=now()) order by f.observed_at desc,f.flag_id) items,
              array_agg(distinct f.value->>'condition') filter(where f.found and not f.expired and f.expires_on is not null and f.observed_at<=now()) conditions
            from v_record_flag_subject f where f.kind='submarket_state'
              and f.value->>'city'=m.city and f.value->>'state'=m.state
              and f.value->>'condition' in ('soft','balanced','tight')) e on true
          order by m.state,m.city limit 1001`, [args.vertical || null,args.city || null,args.state || null])).rows;
        if (rows.length > 1000) throw new ToolError({ error: 'territory_board_limit_exceeded', hint: 'Narrow city or state.' });
        return { schema_version: 'territory-state-board.v1', coverage: 'recorded_geographies_only', submarkets: rows };
      },
    },
    'ideal-client-profile': {
      write: false,
      description: 'Mine recorded closed-won deals into historical fit profiles by vertical, deal type, transaction city and client state. Returns distinct clients, won deal counts and dated deal evidence. Excludes lost/open deals and merged/deleted client parties. Exact optional filters. Closed dates must be recorded and not future. Missing vertical/geography stays unrecorded. Historical fit is a hypothesis, never a prediction or an automatic qualification rule. No commission placeholders or client contact details.',
      inputSchema: marketFilters,
      handler: async (c: Db, _actor: unknown, args: MarketFilter) => {
        const rows = (await c.query(`with wins as (
          select d.id,d.client_id,d.deal_type,d.closed_on,d.city,p.state,
            coalesce(nullif(cl.vertical,''),nullif(d.segment,''),nullif(p.specialty,'')) vertical
          from deal d join client cl on cl.id=d.client_id join party p on p.id=cl.party_id
          where d.phase='closed' and d.outcome='won' and d.closed_on<=current_date
            and cl.merged_into is null and p.merged_into is null and p.deleted_at is null)
          select vertical,deal_type,city,state,count(*)::int won_deals,count(distinct client_id)::int clients,
            jsonb_agg(jsonb_build_object('deal_id',id,'closed_on',closed_on) order by closed_on,id) evidence
          from wins where ($1::text is null or vertical=$1) and ($2::text is null or city=$2)
            and ($3::text is null or state=$3)
          group by vertical,deal_type,city,state order by count(*) desc,vertical,deal_type,city,state limit 1001`,
          [args.vertical || null,args.city || null,args.state || null])).rows;
        if (rows.length > 1000) throw new ToolError({ error: 'client_profile_limit_exceeded', hint: 'Narrow vertical, city or state.' });
        return { schema_version: 'ideal-client-profile.v1', coverage: 'recorded_closed_won_only',
          profiles: rows.map(row => ({ ...row, hypothesis: 'Historical fit, not a prediction or qualification rule.' })) };
      },
    },
    'market-practice-map': {
      write: false,
      description: 'Geographic practice index by recorded vertical, city and state, with practice snapshots, live role references and sourced research evidence. Exact optional filters. Includes organizations with a recorded specialty or client vertical and people linked to a practice. Excludes merged or deleted parties. This is recorded practice coverage, not a complete market census or verified coordinates. Creates no contacts or leads.',
      inputSchema: marketFilters,
      handler: async (c: Db, _actor: unknown, args: MarketFilter) => {
        const rows = (await c.query(`with practices as (
          select p.id,p.ref,p.name,p.kind,p.org_id,p.specialty,p.city,p.county,p.state,
            coalesce(nullif(p.specialty,''),(select nullif(cl.vertical,'') from client cl
              where cl.party_id=p.id and cl.merged_into is null order by cl.id limit 1)) vertical,
            ${researchEvidenceSql('p.id')} evidence,
            coalesce((select jsonb_agg(jsonb_build_object('kind',r.subject_type,'ref',r.ref,'id',r.subject_id) order by r.subject_type,r.subject_id)
              from v_ref_index r where r.party_id=p.id and not r.merged),'[]'::jsonb) roles
          from party p where p.merged_into is null and p.deleted_at is null
            and (p.kind='org' or exists(select 1 from party org where org.id=p.org_id and org.kind='org'
              and org.merged_into is null and org.deleted_at is null)))
          select * from practices where vertical is not null and ($1::text is null or vertical=$1)
            and ($2::text is null or city=$2) and ($3::text is null or state=$3)
          order by vertical,state,city,name,id limit 1001`, [args.vertical || null,args.city || null,args.state || null])).rows;
        if (rows.length > 1000) throw new ToolError({ error: 'practice_map_limit_exceeded', hint: 'Narrow vertical, city or state.' });
        return { schema_version: 'market-practice-map.v1', coverage: 'recorded_practices_only',
          practices: rows.map(({ evidence, ...p }) => ({ ...p, evidence_ref: `party:${p.id}`, research: researchSummary(evidence) })) };
      },
    },
    'prospect-research': {
      write: false,
      description: 'Run before working a handed prospect or contact record. Read exact party UUID or P/L/C/V reference, live roles and sourced findings, including expired, nothing-found and discrepancy evidence. Returns needs_research, needs_review or verified. Does not search the web itself; missing evidence requires checking original public sources and recording findings before outreach. Owners remain contacts; creates no leads, edits no identity and sends nothing. Lead workspace detail runs this evidence check in its read snapshot.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        subject: { type: 'string', minLength: 1, maxLength: 100 },
      }, required: ['subject'] },
      handler: async (c: Db, _actor: unknown, args: { subject: string }) => {
        const rows = (await c.query(`select p.id,p.ref,p.name,p.specialty,p.city,p.state,p.contact_state,
          case when exists(select 1 from client cl where cl.party_id=p.id and cl.merged_into is null) then 'client'
            when exists(select 1 from lead l where l.party_id=p.id) then 'lead'
            when exists(select 1 from vendor v where v.party_id=p.id and v.merged_into is null) then 'vendor' else 'contact' end relationship,
          ${researchEvidenceSql('p.id')} evidence
          from party p where p.merged_into is null and p.deleted_at is null and
            (p.id::text=lower($1) or upper(p.ref)=upper($1) or exists(select 1 from v_ref_index r
              where r.party_id=p.id and not r.merged and (r.subject_id::text=lower($1) or upper(r.ref)=upper($1))))`, [args.subject])).rows;
        if (rows.length !== 1) throw new ToolError({ error: rows.length ? 'prospect_ambiguous' : 'prospect_not_found' });
        const { evidence, ...record } = rows[0];
        return { schema_version: 'prospect-research.v1', ...record, research: researchSummary(evidence), sent: false };
      },
    },
  };
}
