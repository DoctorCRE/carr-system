// Canonical closing anniversaries, shared by Doc, HTTP, and semantic proof.
import type { Actor, Db } from './verb-contract.ts';
import { isCalendarDate } from './calendar-date.ts';

export const CLIENT_ANNIVERSARY_PATH = '/api/v1/intelligence/client-anniversary-read';
export const CLIENT_ANNIVERSARY_SQL = `select d.id::text as deal_id, d.name as deal_name,
 d.closed_on::text, d.phase, d.outcome, d.version as deal_version,
 c.id::text as client_id, p.name as client_name, c.version as client_version,
 p.version as party_version
 from public.deal d join public.client c on c.id=d.client_id
 join public.party p on p.id=c.party_id
 where d.phase in ('closed','invoiced') and d.outcome='won'
 and c.merged_into is null and p.merged_into is null and p.deleted_at is null
 order by d.id limit 1001`;

const inputSchema = { type:'object', additionalProperties:false, properties: {
 horizon_days:{type:'integer',minimum:1,maximum:90},
 limit:{type:'integer',minimum:1,maximum:50}, offset:{type:'integer',minimum:0,maximum:999},
} };
type Query = { horizon_days:number; limit:number; offset:number };
type Source = { client_id:string; client_name:string; deal_id:string; deal_name:string;
 closed_on:string|null; phase:string; outcome:string; deal_version:number; client_version:number; party_version:number };
type Anniversary = Source & { closed_on:string; anniversary_on:string; days_until:number; years:number; evidence_ref:string };
type Response = { schema_version:'client-anniversary-read.v1'; state:'ready'|'empty'|'unavailable'|'needs_input';
 as_of:string; timezone:'America/Chicago'; query:Query; rows:Anniversary[];
 coverage:{scanned:number; skipped_invalid:number; complete:boolean}; next_offset:number|null };
function object(value:unknown): value is Record<string,unknown> {
 return value!==null && typeof value==='object' && !Array.isArray(value);
}
export function parseAnniversaryQuery(value:unknown): Query | null {
 if (!object(value) || Object.keys(value).some(k=>!Object.hasOwn(inputSchema.properties,k))) return null;
 const q={horizon_days:value.horizon_days??30,limit:value.limit??20,offset:value.offset??0};
 if(typeof q.horizon_days!=='number'||!Number.isInteger(q.horizon_days)||q.horizon_days<1||q.horizon_days>90 ||
 typeof q.limit!=='number'||!Number.isInteger(q.limit)||q.limit<1||q.limit>50 ||
 typeof q.offset!=='number'||!Number.isInteger(q.offset)||q.offset<0||q.offset>999) return null;
 return {horizon_days:q.horizon_days,limit:q.limit,offset:q.offset};
}
export function anniversaryQueryFromUrl(url:URL): unknown {
 const args:Record<string,unknown>={};
 for(const [key,value] of url.searchParams){
  if(Object.hasOwn(args,key)||!/^\d+$/.test(value)) return null;
  args[key]=Number(value);
 }
 return args;
}
function source(value:unknown): Source {
 if(!object(value)) throw new Error('invalid_source');
 const {client_id,client_name,deal_id,deal_name,closed_on,phase,outcome,deal_version,client_version,party_version}=value;
 if(typeof client_id!=='string'||!client_id||typeof deal_id!=='string'||!deal_id ||
 typeof client_name!=='string'||!client_name.trim()||typeof deal_name!=='string'||!deal_name.trim() ||
 !(closed_on===null||typeof closed_on==='string')||typeof phase!=='string'||typeof outcome!=='string' ||
 typeof deal_version!=='number'||!Number.isInteger(deal_version)||deal_version<1 ||
 typeof client_version!=='number'||!Number.isInteger(client_version)||client_version<1 ||
 typeof party_version!=='number'||!Number.isInteger(party_version)||party_version<1) throw new Error('invalid_source');
 return {client_id,client_name,deal_id,deal_name,closed_on,phase,outcome,deal_version,client_version,party_version};
}
function dayNumber(day:string):number { return Date.parse(`${day}T00:00:00Z`)/86400000; }
function nextAnniversary(closed:string,asOf:string) {
 const year=Number(asOf.slice(0,4)), original=Number(closed.slice(0,4));
 function inYear(y:number) {
  const date=`${y}-${closed.slice(5)}`;
  return isCalendarDate(date)?date:`${y}-02-28`;
 }
 let next=inYear(year);
 if(next<asOf || year<=original) next=inYear(Math.max(year+1,original+1));
 return {anniversary_on:next,days_until:dayNumber(next)-dayNumber(asOf),years:Number(next.slice(0,4))-original};
}
export function parseClientAnniversaryResponse(value:unknown): Response {
 if(!object(value)||value.schema_version!=='client-anniversary-read.v1'||value.timezone!=='America/Chicago' ||
 typeof value.as_of!=='string'||!isCalendarDate(value.as_of)||!Array.isArray(value.rows) ||
 !['ready','empty','unavailable','needs_input'].includes(String(value.state))) throw new Error('invalid_response');
 const query=parseAnniversaryQuery(value.query);
 if(!query||!object(value.coverage)) throw new Error('invalid_coverage');
 const {scanned,skipped_invalid,complete}=value.coverage;
 if(typeof scanned!=='number'||!Number.isInteger(scanned)||scanned<0||scanned>1001 ||
 typeof skipped_invalid!=='number'||!Number.isInteger(skipped_invalid)||skipped_invalid<0||skipped_invalid>scanned ||
 typeof complete!=='boolean'||complete!==['ready','empty'].includes(String(value.state)) ||
 value.rows.length>query.limit || (value.state==='ready')!==(value.rows.length>0)) throw new Error('invalid_coverage');
 const asOf=value.as_of;
 const rows:Anniversary[]=value.rows.map(item=>{
  const r=source(item);
  if(!object(item)||!r.closed_on||!isCalendarDate(r.closed_on)||r.closed_on>asOf ||
   !['closed','invoiced'].includes(r.phase)||r.outcome!=='won') throw new Error('invalid_row');
  const next=nextAnniversary(r.closed_on,String(value.as_of));
  const evidence_ref=`deal:${r.deal_id}@${r.deal_version}#closed_on`;
  if(next.years<1||next.days_until>query.horizon_days||item.anniversary_on!==next.anniversary_on ||
   item.days_until!==next.days_until||item.years!==next.years||item.evidence_ref!==evidence_ref) throw new Error('invalid_anniversary');
  return {...r,closed_on:r.closed_on,...next,evidence_ref};
 });
 const keys=rows.map(r=>`${r.anniversary_on}/${r.client_id}/${r.deal_id}/${r.closed_on}`);
 if(new Set(keys).size!==keys.length||keys.join()!==[...keys].sort().join()) throw new Error('invalid_order');
 const next=value.next_offset;
 if(!(next===null||(typeof next==='number'&&Number.isInteger(next)&&next===query.offset+rows.length&&next<=999&&rows.length===query.limit))) throw new Error('invalid_pagination');
 const state=value.state;
 if(state!=='ready'&&state!=='empty'&&state!=='unavailable'&&state!=='needs_input') throw new Error('invalid_state');
 return {schema_version:'client-anniversary-read.v1',state,as_of:value.as_of,timezone:'America/Chicago',query,rows,
 coverage:{scanned,skipped_invalid,complete},next_offset:next};
}
export async function readClientAnniversaries({client,actor,args={},now=()=>new Date()}:
 {client:Db;actor:Actor;args?:unknown;now?:()=>Date}):Promise<Response> {
 if(!['joe','dell'].includes(String(actor.slug))) throw new Error('unauthorized');
 const as_of=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Chicago',year:'numeric',month:'2-digit',day:'2-digit'}).format(now());
 const query=parseAnniversaryQuery(args);
 const result:Response={schema_version:'client-anniversary-read.v1',state:'needs_input',as_of,timezone:'America/Chicago',
 query:query??{horizon_days:30,limit:20,offset:0},rows:[],coverage:{scanned:0,skipped_invalid:0,complete:false},next_offset:null};
 if(!query) return parseClientAnniversaryResponse(result);
 try {
  const raw=(await client.query(CLIENT_ANNIVERSARY_SQL)).rows;
  if(raw.length>1000) throw new Error('source_cap');
  result.coverage.scanned=raw.length;
  const rows=new Map<string,Anniversary>();
  for(const item of raw){
   const r=source(item);
   if(!['closed','invoiced'].includes(r.phase)||r.outcome!=='won') continue;
   if(!r.closed_on||!isCalendarDate(r.closed_on)){result.coverage.skipped_invalid++;continue;}
   if(r.closed_on>as_of) continue;
   const next=nextAnniversary(r.closed_on,as_of);
   if(next.days_until>query.horizon_days) continue;
   rows.set(`${r.client_id}/${r.deal_id}/${r.closed_on}`,{...r,closed_on:r.closed_on,...next,evidence_ref:`deal:${r.deal_id}@${r.deal_version}#closed_on`});
  }
  const sorted=[...rows.values()].sort((a,b)=>{
   const key=(r:Anniversary)=>`${r.anniversary_on}/${r.client_id}/${r.deal_id}/${r.closed_on}`;
   return key(a)<key(b)?-1:key(a)>key(b)?1:0;
  });
  result.rows=sorted.slice(query.offset,query.offset+query.limit);
  result.next_offset=query.offset+query.limit<sorted.length?query.offset+query.limit:null;
  result.state=result.rows.length?'ready':'empty';result.coverage.complete=true;
 }catch {result.state='unavailable';result.rows=[];result.coverage.complete=false;result.next_offset=null;}
 return parseClientAnniversaryResponse(result);
}
export function clientAnniversaryTools(){
 return {'read-client-anniversaries':{write:false,inputSchema,
 description:'Read upcoming yearly client closing anniversaries from confirmed closed-won deals and canonical closed_on only. Chicago calendar days; February 29 becomes February 28 in non-leap years. Includes evidence and skipped-invalid coverage. No reminders or greetings. Page with returned next_offset.',
 handler:(client:Db,actor:Actor,args:unknown)=>readClientAnniversaries({client,actor,args})}};
}
