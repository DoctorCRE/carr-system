// Read pending client Tour changes and resolve proposals against the existing cart.
import { authorizeDeliverable } from './deal-deliverables.js';
import { ToolError } from './tool-error.ts';

const uuid = {type:'string',format:'uuid'};
const idem = {type:'string',minLength:1,maxLength:200};
const fail = error => { throw new ToolError({error}); };
const schema = (properties, required=Object.keys(properties)) => ({type:'object',additionalProperties:false,properties,required});
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function selection(value, slug) {
  if (!value || Object.keys(value).sort().join() !== 'items,portal_slug,revision,schema_version' ||
      value.schema_version !== 'client-tour-selection.v1' || value.portal_slug !== slug ||
      !Number.isSafeInteger(value.revision) || value.revision < 1 || !Array.isArray(value.items) || value.items.length > 100)
    fail('proposal_source_invalid');
  const ids = value.items.map(item => {
    if (!item || Object.keys(item).join() !== 'carr_property_id' || !UUID.test(item.carr_property_id)) fail('proposal_source_invalid');
    return item.carr_property_id;
  });
  if (new Set(ids).size !== ids.length) fail('proposal_source_invalid');
  return {revision:value.revision,ids:ids.sort()};
}

export async function pendingClientTourProposals(c, sponsor) {
  const limit = 20;
  const { rows } = await c.query('select * from ops.pending_client_tour_proposals($1)', [sponsor]);
  const items = rows.slice(0, limit).map(row => ({
    ...row,
    line: `${row.deal_name}: client proposed tour list changes (${row.properties_changed} ${row.properties_changed === 1 ? 'property' : 'properties'} changed).`,
  }));
  return { state: rows.length > limit ? 'partial' : items.length ? 'ready' : 'empty',
    items, limit, truncated: rows.length > limit,
    message: rows.length > limit ? 'Showing the first 20 deals with pending client tour proposals.'
      : items.length ? 'Pending client tour proposals.' : 'Pending client tour proposals: none.' };
}

export function clientTourProposalTools({withEnvelope,writeEvent,fetchFn=fetch,now=Date.now}) {
  const sources = new Map();
  async function readSavedList(slug) {
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(slug)) fail('proposal_source_invalid');
    const time = now();
    for (const [key,value] of sources) if (value.until <= time && value.window + 60000 <= time) sources.delete(key);
    const state = sources.get(slug) ?? {failures:0,until:0,window:time,count:0};
    if (sources.size >= 1000 && !sources.has(slug)) fail('proposal_source_capacity');
    if (state.until > time) fail('proposal_source_circuit_open');
    if (state.window + 60000 <= time) { state.window=time; state.count=0; }
    if (state.count >= 20) fail('proposal_source_rate_limited');
    state.count++; sources.set(slug,state);
    const controller = new AbortController();
    const timeout = setTimeout(()=>controller.abort(),5000);
    let reader;
    try {
      const response = await fetchFn(`https://share.doctorcre.com/${slug}/api/selection`,{
        method:'GET',redirect:'error',credentials:'omit',signal:controller.signal,
        headers:{accept:'application/json'},cache:'no-store',
      });
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json') || !response.body) fail('proposal_source_unavailable');
      reader = response.body.getReader();
      let size=0;
      const chunks=[];
      while (true) {
        const {done,value} = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 98304) fail('proposal_source_too_large');
        chunks.push(value);
      }
      const bytes = new Uint8Array(size);
      let offset=0;
      for (const chunk of chunks) {bytes.set(chunk,offset); offset+=chunk.length;}
      const result = selection(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)),slug);
      state.failures=0; state.until=0;
      return result;
    } catch (error) {
      state.failures++;
      if (state.failures >= 2) state.until=now()+60000;
      if (error instanceof ToolError) throw error;
      fail('proposal_source_unavailable');
    } finally {
      clearTimeout(timeout);
      await reader?.cancel().catch(()=>{});
    }
  }
  const authorize = async (c,actor,args) => {
    const deal=(await c.query('select owner,version from public.deal where id=$1',[args.deal_id])).rows[0];
    await authorizeDeliverable(c,actor,args.deal_id);
    if (!deal) fail('proposal_denied');
    await c.query("select set_config('carr.tour_deal_access',$1,true)",[JSON.stringify({slug:actor.slug,id:actor.id||actor.slug,deals:{[args.deal_id]:deal}})]);
  };
  async function query(c,sql,params,column) {
    try { return (await c.query(sql,params)).rows[0]?.[column] ?? fail('proposal_denied'); }
    catch (error) {
      if (/^proposal_[a-z_]+$/.test(error.message)) fail(error.message);
      throw error;
    }
  }
  const mutation = (verb,properties,required,operation) => ({
    write:true,humanOnly:true,
    description:verb === 'pull-client-tour-proposal'
      ? 'Partner pulls one saved portal Tour List into a pending proposal. No shortlist change. No caller URL or portal payload.'
      : 'Partner resolves a client proposal against the bound Tour cart with version checking and an idempotent receipt. Only acceptance changes the shortlist.',
    inputSchema:schema({idempotency_key:idem,deal_id:uuid,...properties},['idempotency_key','deal_id',...required]),
    handler:async (c,actor,args) => {
      await authorize(c,actor,args);
      await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',[args.idempotency_key]);
      return withEnvelope(c,actor,verb,args,async ()=>{
        const result = await operation(c,actor,args);
        await writeEvent(c,actor,verb,'deal',args.deal_id,{field:'client_tour_proposal',new:result,cause:'human_stated',idempotency_key:args.idempotency_key});
        return {ok:true,schema_version:'client-tour-proposals.v1',...result};
      });
    },
  });
  const decision = async (c,actor,args) => ({receipt:await query(c,
    'select ops.accept_client_tour_proposal($1,$2,$3,$4) as receipt',
    [args.deal_id,args.proposal_id,JSON.stringify(args),actor.slug],'receipt')});
  return {
    'pull-client-tour-proposal':mutation('pull-client-tour-proposal',{tour_id:uuid},['tour_id'],async (c,actor,args)=>{
      const source = await query(c,'select ops.client_tour_proposal_source($1,$2,$3) as source',[args.deal_id,args.tour_id,actor.slug],'source');
      const saved = await readSavedList(source.portal_slug);
      return {proposal:await query(c,'select ops.pull_client_tour_proposal($1,$2,$3,$4,$5,$6) as proposal',
        [args.deal_id,args.tour_id,source.portal_slug,saved.revision,JSON.stringify(saved.ids),actor.slug],'proposal')};
    }),
    'list-deal-tour-proposals':{
      write:false,writerConnection:true,
      description:'Partner reads the latest 50 proposals, computed differences against current Tour carts, acceptance versions and total pending badge count for a deal.',
      inputSchema:schema({deal_id:uuid}),
      handler:async (c,actor,args)=>{
        await authorize(c,actor,args);
        return {ok:true,schema_version:'client-tour-proposals.v1',...await query(c,
          'select ops.list_client_tour_proposals($1,$2) as proposals',[args.deal_id,actor.slug],'proposals')};
      },
    },
    'accept-client-tour-proposal':mutation('accept-client-tour-proposal',{
      proposal_id:uuid,expected_selection_version_id:{type:['string','null'],format:'uuid'},
      mode:{type:'string',enum:['use_theirs','per_property']},
      choices:{type:'object',maxProperties:100,additionalProperties:{type:'boolean'},description:'Nonempty map of canonical property UUIDs to desired shortlist membership. SQL validates keys against this proposal.'},
    },['proposal_id','expected_selection_version_id','mode'],decision),
    'dismiss-client-tour-proposal':mutation('dismiss-client-tour-proposal',{proposal_id:uuid},['proposal_id'],
      (c,actor,args)=>decision(c,actor,{...args,mode:'keep_mine'})),
  };
}
