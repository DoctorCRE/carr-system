import { organizationTenantForActor } from "./identity.js";
import { CLIENT_TOUR_FIELD_KEYS, CLIENT_TOUR_PACKET_COLUMNS, requiredTimestamp, PUBLIC_ASSET_REFERENCE_RE } from "./tour-operations-contract.js";
import { clientSafeMetric, isClientRouteLabel, isClientSafeText, normalizeClientText } from "./tour-client-value-safety.js";

const PROPERTY_REF=v=>typeof v==="string"&&/^property:public:[A-Za-z0-9_-]{16,128}$/.test(v);
const PROJECTION_REF=v=>typeof v==="string"&&/^projection:public:[A-Za-z0-9_-]{16,128}$/.test(v);
const COMMENT_REF=v=>typeof v==="string"&&/^comment:public:[A-Za-z0-9_-]{16,128}$/.test(v);
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,DIGEST=/^sha256:[0-9a-f]{64}$/;
const AUTH=new Set(["tenant","tenant_id","organization_tenant_id","actor","actor_id","reviewer","identity","authorization","authorization_class","sponsor","human_slug"]),SCOPES=new Set(["view_packet","view_map","shortlist","comment"]);
const ISSUE=new Set(["idempotency_key","projection_id","token_digest","permission_scopes","expires_at","receipt_digest"]),ROTATE=new Set(["idempotency_key","share_grant_id","projection_id","token_digest","permission_scopes","expires_at","receipt_digest"]),REVOKE=new Set(["idempotency_key","share_grant_id","reason","receipt_digest","revoked_at"]),INTERNAL=new Set(["projection_id","cursor","limit"]),EXCHANGE=new Set(["token_digest","session_digest","session_expires_at","audit_digest"]),SESSION=new Set(["session_digest"]),ASSET=new Set(["session_digest","asset_ref"]),SHORTLIST=new Set(["session_digest","projection_ref","property_ref","shortlisted","idempotency_key"]),COMMENT=new Set(["session_digest","projection_ref","property_ref","comment","idempotency_key"]);
const fail=(E,p)=>{throw new E(p)};
function exact(a,allowed,E){if(!a||typeof a!=="object"||Array.isArray(a))fail(E,{error:"tour_input_invalid",field:"payload"});const authority=Object.keys(a).filter(k=>AUTH.has(k));if(authority.length)fail(E,{error:"caller_authority_field_forbidden",fields:authority});const unknown=Object.keys(a).filter(k=>!allowed.has(k));if(unknown.length)fail(E,{error:"tour_input_unknown_field",fields:unknown});}
function text(v,f,E,max=1000){if(typeof v!=="string"||!v.trim()||v.trim().length>max)fail(E,{error:"tour_input_invalid",field:f});return v.trim();}
function uuid(v,f,E){const x=text(v,f,E,64);if(!UUID.test(x))fail(E,{error:"tour_input_invalid",field:f});return x;}
function digest(v,f,E){const x=text(v,f,E,80);if(!DIGEST.test(x))fail(E,{error:"tour_input_invalid",field:f});return x;}
function timestamp(v,f,E){const x=text(v,f,E,64);if(!requiredTimestamp(x))fail(E,{error:"tour_input_invalid",field:f});return x;}
function tenant(a,E){if(!a||typeof a.id!=="string"||!a.id.trim())fail(E,{error:"tour_actor_context_required"});const t=organizationTenantForActor(a);if(typeof t!=="string"||!t)fail(E,{error:"tour_tenant_context_required"});return t;}
function scopes(v,E){if(!Array.isArray(v)||!v.length||v.length>SCOPES.size||new Set(v).size!==v.length||v.some(x=>!SCOPES.has(x)))fail(E,{error:"tour_share_scope_invalid"});return v.slice();}
function cursor(v,E){if(v===null)return null;const x=text(v,"cursor",E,9);if(!/^[0-9]{1,9}$/.test(x))fail(E,{error:"tour_input_invalid",field:"cursor"});return x;}
function limit(v,E){if(!Number.isInteger(v)||v<1||v>100)fail(E,{error:"tour_input_invalid",field:"limit"});return v;}
function reason(v,E){const x=text(v,"reason",E,240);if(!/^[A-Za-z0-9 .,:;()/_-]+$/.test(x))fail(E,{error:"tour_share_revocation_reason_invalid"});return x;}
function publicRef(v,f,check,E){if(!check(v))fail(E,{error:"tour_input_invalid",field:f});return v;}
function commentText(v,E){const x=text(v,"comment",E,1000);if(/[\u0000-\u001f\u007f]/.test(x))fail(E,{error:"tour_input_invalid",field:"comment"});return x;}
const schema=(properties,required)=>({type:"object",additionalProperties:false,properties,required}),idem={idempotency_key:{type:"string"}};
// Client packet stops (V5-J303). Built key by key from an explicit list, so a
// column the database adds later is dropped here until someone allowlists it:
// the envelope (opaque property ref, stop order and a short stop marker) plus
// the columns of CLIENT_TOUR_FIELD_KEYS. Every value then passes the shared
// client value-safety rule (tour-client-value-safety.js, the same rule the
// database seal and the PDF renderer apply). All or nothing, like the database
// (ops.tour_public_projection_client_safe) and the PDF renderer: one unsafe
// value, one stop without a safe name/address, or one stop label that is not
// a client marker refuses the WHOLE packet or map, so the list, the map and
// the PDF can never show a client different stops. Access and owner details,
// notes, caveats, photos, appointment times and internal ids never leave,
// whether as their own key or typed inside an allowed one.
const CLIENT_STRUCTURED_COLUMNS=new Set(["size","asking_economics"]);
function clientStop(v){if(!v||typeof v!=="object"||Array.isArray(v)||!PROPERTY_REF(v.property_ref)||!Number.isInteger(v.route_sequence)||v.route_sequence<1||!isClientRouteLabel(v.route_label)||!isClientSafeText(v.name)||!isClientSafeText(v.address))return undefined;const out={property_ref:v.property_ref,route_sequence:v.route_sequence,route_label:v.route_label};for(const field of CLIENT_TOUR_FIELD_KEYS){const column=CLIENT_TOUR_PACKET_COLUMNS[field];if(!Object.hasOwn(v,column)||v[column]===null)continue;if(CLIENT_STRUCTURED_COLUMNS.has(column)){const x=clientSafeMetric(v[column]);if(x===undefined)return undefined;out[column]=x;}else{if(!isClientSafeText(v[column]))return undefined;out[column]=normalizeClientText(v[column]);}}return out;}
function clientPoint(p){if(!p||typeof p!=="object"||Array.isArray(p)||!PROPERTY_REF(p.property_ref)||!Number.isInteger(p.route_sequence)||p.route_sequence<1||!isClientRouteLabel(p.route_label)||!Number.isFinite(p.latitude)||p.latitude< -90||p.latitude>90||!Number.isFinite(p.longitude)||p.longitude< -180||p.longitude>180)return undefined;return{latitude:p.latitude,longitude:p.longitude,property_ref:p.property_ref,route_sequence:p.route_sequence,route_label:p.route_label};}
function allOrNothing(items,project){if(!Array.isArray(items))return null;const out=items.map(project);return out.every(Boolean)?out:null;}
export function projectTourClientPacket(v){if(!v||typeof v!=="object"||Array.isArray(v))return null;const stops=allOrNothing(v.stops,clientStop);if(!stops)return null;const out={};if(typeof v.as_of==="string")out.as_of=v.as_of;out.stops=stops;return out;}
export function projectTourClientMap(v){if(!v||typeof v!=="object"||Array.isArray(v))return null;const points=allOrNothing(v.points,clientPoint);if(!points)return null;const out={};if(typeof v.as_of==="string")out.as_of=v.as_of;out.points=points;return out;}
export function projectTourPublicAsset(v,assetRef){if(!PUBLIC_ASSET_REFERENCE_RE.test(assetRef||""))return null;const out={asset_ref:assetRef};if(v&&typeof v==="object"&&!Array.isArray(v)){if(typeof v.media_type==="string")out.media_type=v.media_type;if(Number.isInteger(v.content_length)&&v.content_length>=0)out.content_length=v.content_length;for(const k of ["alt","caption"])if(typeof v[k]==="string"&&v[k].length<=1000)out[k]=v[k];}return out;}
// Browser record-layer factory: digest-only and deliberately actor/tenant-free.
function projectInternalSharing(value){if(value===null||["string","number","boolean"].includes(typeof value))return value;if(Array.isArray(value))return value.map(projectInternalSharing).filter(item=>item!==undefined);if(!value||typeof value!=="object")return undefined;const allowed=new Set(["grants","items","cursor","has_more","share_grant_id","status","permission_scopes","expires_at","revoked_at","reason","property_ref","route_sequence","route_label","created_at","updated_at"]),out={};for(const [key,item] of Object.entries(value)){if(!allowed.has(key))continue;if(key==="property_ref"){if(PROPERTY_REF(item))out[key]=item;continue;}if(key==="share_grant_id"){if(UUID.test(item||""))out[key]=item;continue;}if(key==="permission_scopes"){if(Array.isArray(item))out[key]=item.filter(scope=>SCOPES.has(scope));continue;}const safe=projectInternalSharing(item);if(safe!==undefined)out[key]=safe;}return out;}
function projectComment(value){if(!value||typeof value!=="object"||!COMMENT_REF(value.comment_ref)||typeof value.comment!=="string"||value.comment.length>1000||typeof value.created_at!=="string")return null;return{comment_ref:value.comment_ref,comment:value.comment,created_at:value.created_at};}
function projectFeedbackItem(value){if(!value||!PROPERTY_REF(value.property_ref)||(value.shortlisted!==null&&typeof value.shortlisted!=="boolean")||!Array.isArray(value.comments))return null;const comments=allOrNothing(value.comments,projectComment);return comments?{property_ref:value.property_ref,shortlisted:value.shortlisted,comments}:null;}
export function projectTourClientFeedback(value){if(!value||!PROJECTION_REF(value.projection_ref)||!Array.isArray(value.permission_scopes)||!Array.isArray(value.items))return null;const items=allOrNothing(value.items,item=>item&&PROPERTY_REF(item.property_ref)?{property_ref:item.property_ref}:null);return items?{projection_ref:value.projection_ref,permission_scopes:value.permission_scopes.filter(scope=>scope==="shortlist"||scope==="comment"),items}:null;}
function projectFeedbackAck(value){return value?.saved===true?{saved:true}:null;}
function projectBrokerFeedback(value){if(!value||!UUID.test(value.projection_id||"")||!Array.isArray(value.items))return null;const items=allOrNothing(value.items,item=>{const projected=projectFeedbackItem(item);return projected&&isClientRouteLabel(item.route_label)?{...projected,route_label:item.route_label}:projected;});return items?{projection_id:value.projection_id,items}:null;}
export function tourSharingBrowserAccess({ToolError}) {
  async function read(c,a,allowed,seam,field,project) {
    exact(a,allowed,ToolError);
    const sd=digest(a.session_digest,"session_digest",ToolError);
    const params=allowed===ASSET?[sd,text(a.asset_ref,"asset_ref",ToolError,300)]:[sd];
    if(allowed===ASSET&&!PUBLIC_ASSET_REFERENCE_RE.test(params[1]))fail(ToolError,{error:"tour_public_asset_invalid"});
    const result=await c.query(`select ops.${seam}($1::text${allowed===ASSET?",$2::text":""}) as ${field} /* tour-sharing-browser:${field} */`,params);
    const value=result.rows[0]?.[field];
    if(!value)fail(ToolError,{error:"tour_share_access_refused"});
    const projected=project(value,params[1]);
    if(!projected)fail(ToolError,{error:"tour_share_access_refused"});
    return {ok:true,[field]:projected};
  }
  async function write(c,a,allowed,seam,kind) {
    exact(a,allowed,ToolError);
    const params=[digest(a.session_digest,"session_digest",ToolError),
      publicRef(a.projection_ref,"projection_ref",PROJECTION_REF,ToolError),
      publicRef(a.property_ref,"property_ref",PROPERTY_REF,ToolError),
      kind==="shortlist"?a.shortlisted:commentText(a.comment,ToolError),
      uuid(a.idempotency_key,"idempotency_key",ToolError)];
    if(kind==="shortlist"&&typeof params[3]!=="boolean")fail(ToolError,{error:"tour_input_invalid",field:"shortlisted"});
    const sql=`select ops.${seam}($1::text,$2::text,$3::text,$4::${kind==="shortlist"?"boolean":"text"},$5::uuid) as feedback /* tour-sharing-browser:${kind} */`;
    const result=await c.query(sql,params);
    const feedback=projectFeedbackAck(result.rows[0]?.feedback,kind);
    if(!feedback)fail(ToolError,{error:"tour_share_access_refused"});
    return {ok:true,feedback};
  }
  return {
    exchange:async(c,a)=>{
      exact(a,EXCHANGE,ToolError);
      const payload=[digest(a.token_digest,"token_digest",ToolError),digest(a.session_digest,"session_digest",ToolError),timestamp(a.session_expires_at,"session_expires_at",ToolError),digest(a.audit_digest,"audit_digest",ToolError)];
      const result=await c.query("select ops.exchange_tour_share_token($1::text,$2::text,$3::timestamptz,$4::text) as exchange /* tour-sharing-browser:exchange */",payload);
      const exchange=result.rows[0]?.exchange;
      if(!exchange||typeof exchange!=="object")fail(ToolError,{error:"tour_share_access_refused"});
      return {ok:true,expires_at:typeof exchange.expires_at==="string"?exchange.expires_at:payload[2],permission_scopes:Array.isArray(exchange.permission_scopes)?exchange.permission_scopes.filter(scope=>SCOPES.has(scope)):[]};
    },
    readPacket:(c,a)=>read(c,a,SESSION,"read_tour_share_packet","packet",projectTourClientPacket),
    readMap:(c,a)=>read(c,a,SESSION,"read_tour_share_map","map",projectTourClientMap),
    resolveAsset:(c,a)=>read(c,a,ASSET,"resolve_tour_public_asset","asset",projectTourPublicAsset),
    readFeedback:(c,a)=>read(c,a,SESSION,"read_tour_share_feedback","feedback",projectTourClientFeedback),
    shortlist:(c,a)=>write(c,a,SHORTLIST,"write_tour_share_shortlist","shortlist"),
    comment:(c,a)=>write(c,a,COMMENT,"write_tour_share_comment","comment"),
  };
}
export function tourSharingTools({withEnvelope,writeEvent,ToolError}){const internalSchema=schema({projection_id:{type:"string"},cursor:{type:["string","null"],pattern:"^[0-9]{1,9}$"},limit:{type:"integer",minimum:1,maximum:100}},[...INTERNAL]);const authorityWrite=({verb,allowed,inputSchema,build,sql,entity,audit,result})=>({write:true,authorityOnly:true,description:`Authority-only digest-only ${entity} operation.`,inputSchema,handler:async(c,a,x)=>{exact(x,allowed,ToolError);const payload=build(x,a);return withEnvelope(c,a,verb,x,async()=>{const queryResult=await c.query(sql,payload),id=queryResult.rows[0]?.share_grant_id;if(!id)fail(ToolError,{error:"tour_write_refused",entity});const safe=result(id,x,payload);await writeEvent(c,a,verb,"tour_share_grant",id,audit(x,payload));return safe;});}});return{"issue-tour-share-grant":authorityWrite({verb:"issue-tour-share-grant",allowed:ISSUE,entity:"share_grant",inputSchema:schema({...idem,projection_id:{type:"string"},token_digest:{type:"string"},permission_scopes:{type:"array",minItems:1,items:{type:"string"}},expires_at:{type:"string"},receipt_digest:{type:"string"}},[...ISSUE]),build:(x,a)=>[tenant(a,ToolError),uuid(x.projection_id,"projection_id",ToolError),digest(x.token_digest,"token_digest",ToolError),JSON.stringify(scopes(x.permission_scopes,ToolError)),timestamp(x.expires_at,"expires_at",ToolError),digest(x.receipt_digest,"receipt_digest",ToolError),a.id],sql:"select ops.issue_tour_share_grant($1::text,$2::uuid,$3::text,$4::jsonb,$5::timestamptz,$6::text,$7::text) as share_grant_id /* tour-sharing:issue */",audit:(x,p)=>({field:"status",new:{status:"active",projection_id:p[1],permission_scopes:x.permission_scopes,expires_at:p[4]},idempotency_key:x.idempotency_key}),result:id=>({ok:true,share_grant_id:id,status:"active"})}),"rotate-tour-share-grant":authorityWrite({verb:"rotate-tour-share-grant",allowed:ROTATE,entity:"share_grant_rotation",inputSchema:schema({...idem,share_grant_id:{type:"string"},projection_id:{type:"string"},token_digest:{type:"string"},permission_scopes:{type:"array",minItems:1,items:{type:"string"}},expires_at:{type:"string"},receipt_digest:{type:"string"}},[...ROTATE]),build:(x,a)=>[tenant(a,ToolError),uuid(x.share_grant_id,"share_grant_id",ToolError),uuid(x.projection_id,"projection_id",ToolError),digest(x.token_digest,"token_digest",ToolError),JSON.stringify(scopes(x.permission_scopes,ToolError)),timestamp(x.expires_at,"expires_at",ToolError),digest(x.receipt_digest,"receipt_digest",ToolError),a.id],sql:"select ops.rotate_tour_share_grant($1::text,$2::uuid,$3::uuid,$4::text,$5::jsonb,$6::timestamptz,$7::text,$8::text) as share_grant_id /* tour-sharing:rotate */",audit:(x,p)=>({field:"status",old:{share_grant_id:p[1],status:"active"},new:{status:"active",projection_id:p[2],permission_scopes:x.permission_scopes,expires_at:p[5]},idempotency_key:x.idempotency_key}),result:(id,x,p)=>({ok:true,share_grant_id:id,supersedes_share_grant_id:p[1],status:"active"})}),"revoke-tour-share-grant":authorityWrite({verb:"revoke-tour-share-grant",allowed:REVOKE,entity:"share_grant_revocation",inputSchema:schema({...idem,share_grant_id:{type:"string"},reason:{type:"string"},receipt_digest:{type:"string"},revoked_at:{type:"string"}},[...REVOKE]),build:(x,a)=>[tenant(a,ToolError),uuid(x.share_grant_id,"share_grant_id",ToolError),reason(x.reason,ToolError),digest(x.receipt_digest,"receipt_digest",ToolError),timestamp(x.revoked_at,"revoked_at",ToolError),a.id],sql:"select ops.revoke_tour_share_grant($1::text,$2::uuid,$3::text,$4::text,$5::timestamptz,$6::text) as share_grant_id /* tour-sharing:revoke */",audit:(x,p)=>({field:"status",new:{status:"revoked",reason:p[2],revoked_at:p[4]},idempotency_key:x.idempotency_key}),result:id=>({ok:true,share_grant_id:id,status:"revoked"})}),"read-tour-feedback":{writerConnection:true,description:"Read client shortlist and comments for one sealed Tour projection.",inputSchema:internalSchema,handler:async(c,a,x)=>{exact(x,INTERNAL,ToolError);const params=[tenant(a,ToolError),uuid(x.projection_id,"projection_id",ToolError),a.id,cursor(x.cursor,ToolError),limit(x.limit,ToolError)];const r=await c.query("select ops.read_tour_feedback($1::text,$2::uuid,$3::text,$4::text,$5::integer) as feedback /* tour-sharing:feedback */",params);const feedback=projectBrokerFeedback(r.rows[0]?.feedback);if(!feedback)fail(ToolError,{error:"tour_feedback_not_found"});return{ok:true,feedback};}},"read-tour-sharing-library":{writerConnection:true,description:"Authenticated internal sharing library by immutable projection.",inputSchema:internalSchema,handler:async(c,a,x)=>{exact(x,INTERNAL,ToolError);const r=await c.query("select ops.read_tour_sharing_library($1::text,$2::uuid,$3::text,$4::text,$5::integer) as library /* tour-sharing:library */",[tenant(a,ToolError),uuid(x.projection_id,"projection_id",ToolError),a.id,cursor(x.cursor,ToolError),limit(x.limit,ToolError)]),v=projectInternalSharing(r.rows[0]?.library);if(!v)fail(ToolError,{error:"tour_share_library_not_found"});return{ok:true,library:v};}}};}
