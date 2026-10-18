// Local readers submit derived invoice facts. No provider, raw message or send path.
const normal = value => typeof value === "string" ? value.trim().replace(/\s+/g, " ").toLowerCase() : "";
const candidateDeals = (invoice,deals) => deals.filter(d=>normal(d.name)===normal(invoice.deal_name));
const dateText = value => value instanceof Date ? value.toISOString().slice(0, 10) : value;
const schema = properties => ({type:"object",additionalProperties:false,properties});

// Exact deal name is necessary. A corroborating client or complete premises
// address is necessary too. Contradictory supplied fields and multiple matches
// refuse automation, regardless of how many other fields happen to agree.
export function planInvoiceCloses(invoices, deals, mailbox, now) {
  const simulated=deals.map(d=>({...d}));
  return invoices.filter(i=>i.status === "captured").map(i=>{
    i={...i,email_date:dateText(i.email_date)};
    const candidates=candidateDeals(i,simulated);
    const matches=candidates.filter(d=>{
      const client=normal(i.client_name), address=normal(i.property_address);
      return (client || address) && (!client || client===normal(d.client_name)) &&
        (!address || (d.property_addresses||[]).some(a=>normal(a)===address));
    });
    const trusted=!!normal(mailbox) && normal(i.from_address)===normal(mailbox);
    const dated=Number.isFinite(Date.parse(i.occurred_at)) && Date.parse(i.occurred_at)<=Date.parse(now);
    const d=matches.length===1 ? matches[0] : null;
    const conflict=d && d.invoiced_on && dateText(d.invoiced_on)!==i.email_date;
    const confident=trusted && dated && !!d && !conflict;
    const explanation=!trusted ? "Confirm invoicing mailbox" : !dated ? "Confirm email date" :
      matches.length>1 ? "Multiple deals match" : !d ? "Confirm deal and client or property" :
      conflict ? "Deal has a different invoice date" : null;
    const move={invoice_id:i.id,deal_id:d?.id || null,deal_name:i.deal_name,candidate_deal_ids:candidates.map(d=>d.id),
      base_version:d?.version || null,from_phase:d?.phase || null,to_phase:"closed",invoiced_on:i.email_date,
      reason:`Invoice received ${i.email_date}`,evidence_ref:i.evidence_ref,
      status:confident ? "applied" : "proposed",needs_confirmation:explanation};
    if(confident) {d.phase="closed";d.invoiced_on=i.email_date;d.version++;}
    return move;
  });
}
const DEALS_SQL=`select d.id,d.name,d.phase,d.invoiced_on,d.version,p.name as client_name,
  coalesce((select array_agg(distinct concat_ws(', ',b.address,nullif(trim(s.suite),''),b.city,concat_ws(' ',b.state,b.zip))) from premises pr join premises_space ps on ps.premises_id=pr.id
    join space s on s.id=ps.space_id join building b on b.id=s.building_id
    where pr.deal_id=d.id and b.merged_into is null
      and nullif(trim(b.address),'') is not null and nullif(trim(b.city),'') is not null
      and nullif(trim(b.state),'') is not null and nullif(trim(b.zip),'') is not null),'{}') as property_addresses
  from deal d join client cl on cl.id=d.client_id join party p on p.id=cl.party_id
  where cl.merged_into is null and p.merged_into is null and p.deleted_at is null order by d.id`;

export function invoiceAutomation({withEnvelope,writeEvent,ToolError,updateDeal,lockDealField,invoicingMailbox}) {
  const fail=error=>{throw new ToolError({error});};
  async function snapshot(c,lock=false) {
    const now=(await c.query("select now() as now")).rows[0].now;
    const invoices=(await c.query("select * from deal_invoice_email where status='captured' order by occurred_at,id"+(lock?" for update":""))).rows;
    const deals=(await c.query(DEALS_SQL+(lock?" for update of d":""))).rows;
    return {now,invoices,deals};
  }
  async function preview(c) {
    const s=await snapshot(c);
    return planInvoiceCloses(s.invoices,s.deals,invoicingMailbox,s.now);
  }
  async function fieldEvents(c,id) {
    return (await c.query(`select distinct on (field) id,field from event
      where subject_type='deal' and subject_id=$1 and field in ('phase','invoiced_on')
      order by field,recorded_at desc,id desc`,[id])).rows;
  }
  async function apply(c,actor,args) {
    // Match update-deal's phase advisory-lock -> row-lock order. The shared job
    // acquires invoice rows in occurrence order, serializing competing runs.
    const initial=await snapshot(c);
    for(const d of initial.deals) await lockDealField(c,d.id,"phase");
    // Membership tables prevent phantoms; reference rows protect every matching
    // fact, including currently retired rows that could become candidates.
    // Order: phase advisory locks, membership tables, party/space/building rows,
    // invoice/deal rows. No new phase lock is acquired under a table lock.
    await c.query("lock table client,deal,premises,premises_space in share mode");
    await c.query(`select p.id from party p where exists(select 1 from client cl where cl.party_id=p.id)
      order by p.id for share of p`);
    await c.query(`select s.id from space s where exists(select 1 from premises_space ps where ps.space_id=s.id)
      order by s.id for share of s`);
    await c.query(`select b.id from building b where exists(select 1 from space s join premises_space ps on ps.space_id=s.id where s.building_id=b.id)
      order by b.id for share of b`);
    const s=await snapshot(c,true), results=[];
    const priorCandidates=new Map(s.invoices.map(i=>[i.id,candidateDeals(i,initial.deals).map(d=>d.id)]));
    // Replan over the entire current set. Changed membership cannot silently
    // introduce a close whose phase lock was absent from the initial census.
    const stableInvoices=s.invoices.filter(i=>{
      const ids=candidateDeals(i,s.deals).map(d=>d.id);
      return JSON.stringify(ids)===JSON.stringify(priorCandidates.get(i.id));
    });
    const planned=new Map(planInvoiceCloses(stableInvoices,s.deals,invoicingMailbox,s.now).map(m=>[m.invoice_id,m]));
    for(const invoice of s.invoices) {
      const move=planned.get(invoice.id)||planInvoiceCloses([invoice],s.deals,invoicingMailbox,s.now)[0];
      if(!planned.has(invoice.id)&&move.status==="applied") {
        move.status="proposed";move.needs_confirmation="Deal candidates changed; preview again";
      }
      if(move.status!=="applied") {results.push(move);continue;}
      const d=s.deals.find(d=>d.id===move.deal_id);
      await updateDeal(c,actor,{idempotency_key:`${args.idempotency_key}:invoice:${move.invoice_id}`,
        deal:d.id,base_version:d.version,fields:{phase:"closed",invoiced_on:move.invoiced_on}});
      const ids=await fieldEvents(c,d.id);
      await c.query(`update deal_invoice_email set status='applied',deal_id=$2,prior_phase=$3,prior_invoiced_on=$4,
        phase_event_id=$5,invoice_event_id=$6,applied_by=$7,applied_at=now(),reason=$8 where id=$1`,
        [move.invoice_id,d.id,d.phase,d.invoiced_on,ids.find(e=>e.field==="phase").id,ids.find(e=>e.field==="invoiced_on").id,actor.id,move.reason]);
      await writeEvent(c,actor,"advance-leads","deal",d.id,{old:{phase:d.phase,invoiced_on:dateText(d.invoiced_on)},
        new:{phase:"closed",invoiced_on:move.invoiced_on,invoice_move_id:move.invoice_id,reason:move.reason,evidence_ref:move.evidence_ref},cause:"automation_job"});
      const fresh=(await c.query("select version from deal where id=$1",[d.id])).rows[0];
      d.version=fresh.version;d.phase="closed";d.invoiced_on=move.invoiced_on;
      results.push(move);
    }
    return results;
  }
  const tools={
    "record-deal-invoice": {
      write:true,description:"Capture a dated invoice fact from local mail. Exact deal name plus client or property is required for automatic close; uncertain matches remain partner proposals. No email body or sending.",
      inputSchema:{...schema({idempotency_key:{type:"string"},native_ref:{type:"string",minLength:1,maxLength:500},
        from_address:{type:"string",maxLength:320},deal_name:{type:"string",minLength:1,maxLength:500},
        client_name:{type:"string",maxLength:500},property_address:{type:"string",maxLength:1000},occurred_at:{type:"string",format:"date-time"}}),
        required:["idempotency_key","native_ref","from_address","deal_name","occurred_at"]},
      handler:(c,actor,args)=>withEnvelope(c,actor,"record-deal-invoice",args,async()=>{
        const now=(await c.query("select now() as now")).rows[0].now;
        if(!/^local-mail:\S/.test(args.native_ref||"") || !normal(args.deal_name) ||
          !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(args.from_address||"") ||
          !/^\d{4}-\d{2}-\d{2}T/.test(args.occurred_at||"") || !Number.isFinite(Date.parse(args.occurred_at)) || Date.parse(args.occurred_at)>Date.parse(now)) fail("invalid_invoice_evidence");
        // Preserve the email's local calendar date, including its offset.
        const emailDate=args.occurred_at.slice(0,10);
        if(new Date(`${emailDate}T00:00:00Z`).toISOString().slice(0,10)!==emailDate) fail("invalid_invoice_evidence");
        const row=(await c.query(`insert into deal_invoice_email(evidence_ref,from_address,deal_name,client_name,property_address,occurred_at,email_date,created_by)
          values($1,$2,$3,$4,$5,$6,$7,$8) on conflict(evidence_ref) do nothing returning id`,
          [args.native_ref,normal(args.from_address),args.deal_name.trim(),args.client_name?.trim()||null,args.property_address?.trim()||null,args.occurred_at,emailDate,actor.id])).rows[0];
        if(!row) fail("invoice_already_captured");
        await writeEvent(c,actor,"record-deal-invoice","deal_invoice_email",row.id,{new:{evidence_ref:args.native_ref,email_date:emailDate},cause:"ingest_email"});
        return {ok:true,invoice_id:row.id,evidence_ref:args.native_ref};
      }),
    },
    "invoice-close-queue": {
      write:false,description:"Invoice close proposals awaiting a confident deal match, and applied/undone invoice closes with reason, evidence and undo identifiers.",
      inputSchema:schema({}),handler:async c=>({proposals:(await preview(c)).filter(m=>m.status==="proposed"),
        moves:(await c.query(`select i.id as invoice_id,i.deal_id,i.status,i.reason,i.evidence_ref,i.email_date as invoiced_on,
          i.prior_phase,i.applied_at,i.applied_by,i.undone_at,i.undone_by,d.version as base_version
          from deal_invoice_email i join deal d on d.id=i.deal_id where i.status in ('applied','undone') order by i.occurred_at desc,i.id`)).rows}),
    },
    "undo-invoice-close": {
      write:true,humanOnly:true,description:"Restore the phase and invoice date before an invoice close. Record the partner who undid it; refuse newer phase or invoice-date work.",
      inputSchema:{...schema({idempotency_key:{type:"string"},invoice_id:{type:"string",format:"uuid"},base_version:{type:"integer"}}),required:["idempotency_key","invoice_id","base_version"]},
      handler:(c,actor,args)=>withEnvelope(c,actor,"undo-invoice-close",args,async()=>{
        if(!actor.human) fail("human_approval_required");
        const m=(await c.query("select * from deal_invoice_email where id=$1",[args.invoice_id])).rows[0];
        if(!m || m.status!=="applied") fail("invoice_close_not_applied");
        await lockDealField(c,m.deal_id,"phase");
        const d=(await c.query("select phase,invoiced_on,version from deal where id=$1 for update",[m.deal_id])).rows[0];
        const current=(await c.query("select status from deal_invoice_email where id=$1 for update",[m.id])).rows[0];
        const ids=await fieldEvents(c,m.deal_id);
        if(current.status!=="applied" || d.version!==args.base_version || d.phase!=="closed" || dateText(d.invoiced_on)!==dateText(m.email_date) ||
          ids.find(e=>e.field==="phase")?.id!==m.phase_event_id || ids.find(e=>e.field==="invoiced_on")?.id!==m.invoice_event_id) fail("newer_invoice_change_exists");
        await updateDeal(c,actor,{idempotency_key:`${args.idempotency_key}:restore`,deal:m.deal_id,base_version:d.version,
          fields:{phase:m.prior_phase,invoiced_on:dateText(m.prior_invoiced_on)}});
        await c.query("update deal_invoice_email set status='undone',undone_at=now(),undone_by=$2 where id=$1",[m.id,actor.id]);
        await writeEvent(c,actor,"undo-invoice-close","deal",m.deal_id,{old:{phase:"closed",invoiced_on:dateText(m.email_date)},new:{phase:m.prior_phase,invoiced_on:dateText(m.prior_invoiced_on),undone_invoice_id:m.id,undone_by:actor.id,reason:`Undid: ${m.reason}`,evidence_ref:m.evidence_ref},cause:"human_correction"});
        return {ok:true,deal_id:m.deal_id,phase:m.prior_phase,invoiced_on:dateText(m.prior_invoiced_on),undone_by:actor.id};
      }),
    },
  };
  return {tools,preview,apply};
}
