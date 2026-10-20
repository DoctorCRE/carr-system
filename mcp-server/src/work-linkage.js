import { readableWorkRequestSql } from './work-request-read.js';
const text=value=>typeof value==='string' && !!value.trim() && !/[\u0000-\u001f]/.test(value);
const record=value=>value && typeof value==='object' && !Array.isArray(value);
const pullRequestUrl=value=>typeof value==='string'&&/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*$/.test(value);
export function unavailableLinkage(subject, reason = 'source_linkage_not_observed', references = []) {
 return {schema:'work-linkage.v1',state:'unavailable',subject,references,reason};
}
export function projectTaskLinkage(row,id) {
 const subject={board_id:row.board_id,task_id:id,version:Number(row.version)};
 const tasks=row.snapshot_json?.tasks,task=tasks?.[id],references=[],seen=new Set();
 if(!record(task) || !text(row.board_id) || !Number.isSafeInteger(subject.version) || subject.version<1)
  return unavailableLinkage(subject,'source_binding_invalid');
 if(task.linkage_metadata_unavailable!=null || ['dependency_refs','depends_on','blocked_by','blocker_ref','blocker_task_id','parent_refs','related_refs','dependencies'].some(key=>task[key]!=null) || task.held?.blocker!=null)
  return unavailableLinkage(subject,'unsupported_recorded_association');
 const ancestors={plan:['plan_ref','plan_id','plan'],slice:['slice_ref','slice_id','slice'],job:['job_ref','job_id'],parent:['parent_ref','parent_id','parent']};
 const fields=[...Object.values(ancestors).flat(),'work_request','work_request_ref','work_request_id','canonical_work_ref','human_ref','pr_url'];
 if(task.blocked_by_task_ids!=null && (!Array.isArray(task.blocked_by_task_ids)||!task.blocked_by_task_ids.every(text))
  || task.parent_task_id!=null && !text(task.parent_task_id)
  || fields.some(key=>task[key]!=null && !text(task[key]))
  || task.related!=null && (!Array.isArray(task.related)||!task.related.every(ref=>record(ref)&&text(ref.id)&&text(ref.kind))))
  return unavailableLinkage(subject,'reference_structure_invalid');
 const add=(relation,ref,title,state,target)=>{
  const key=JSON.stringify([relation,ref]);if(seen.has(key))return;seen.add(key);
  references.push({relation,ref,title,state,target});
 };
 for(const [relation,refs] of [['blocked_by',task.blocked_by_task_ids||[]],['parent',task.parent_task_id?[task.parent_task_id]:[]]]) {
  for(const ref of refs) {
   const target=ref!==id && Object.hasOwn(tasks,ref) && record(tasks[ref]) ? tasks[ref]:null;
   add(relation,ref,target?.title||ref,target?'resolved':'unresolved',target?{board_id:row.board_id,task_id:ref}:null);
  }
 }
 for(const [relation,fields] of Object.entries(ancestors))
  for(const field of fields) if(task[field]) add(relation,task[field],task[field],'unresolved',null);
 for(const ref of task.related||[]) add(Object.hasOwn(ancestors,ref.kind)||ref.kind==='work_request'?ref.kind:'related',ref.id,ref.title||ref.id,ref.kind==='work_request'?'unavailable':'unresolved',null);
 for(const field of ['work_request','work_request_ref','work_request_id','canonical_work_ref','human_ref'])
  if(task[field]) add('work_request',task[field],task[field],'unavailable',null);
 const prs=[...(task.pr!=null&&!pullRequestUrl(task.pr)?[{repo:task.repo,number:task.pr}]:[]),...(Array.isArray(task.pr_links)?task.pr_links:[])];
 if(task.pr_links!=null&&!Array.isArray(task.pr_links))return unavailableLinkage(subject,'reference_structure_invalid',references);
 for(const pr of prs) {
  const valid=record(pr)&&typeof pr.repo==='string'&&/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(pr.repo)&&Number.isSafeInteger(Number(pr.number))&&Number(pr.number)>0;
  if(!valid)return unavailableLinkage(subject,'reference_structure_invalid',references);
  const url='https://github.com/'+pr.repo+'/pull/'+Number(pr.number);
  add('pull_request',url,'PR #'+pr.number,'resolved',{url});
 }
 for(const url of [task.pr_url,pullRequestUrl(task.pr)?task.pr:null].filter(Boolean)) {
  if(!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*$/.test(url))
   return unavailableLinkage(subject,'reference_structure_invalid',references);
  add('pull_request',url,url,'resolved',{url});
 }
 if(!references.length && task.linkage_metadata_version!==1)
  return unavailableLinkage(subject);
 return {schema:'work-linkage.v1',state:'complete',subject,references,reason:null};
}
export function projectBoardLinkage(row) {
 if (!record(row?.snapshot_json?.tasks)) return row;
 const tasks=Object.fromEntries(Object.entries(row.snapshot_json.tasks).map(([id,task])=>[id,record(task)?{...task,linkage:projectTaskLinkage(row,id)}:task]));
 return {...row,snapshot_json:{...row.snapshot_json,tasks}};
}

export async function observeBoardLinkage(client,rows,tenant,sponsor) {
 const projected=rows.map(projectBoardLinkage);
 const references=projected.flatMap(row=>Object.values(row?.snapshot_json?.tasks||{}).flatMap(task=>task?.linkage?.references||[]));
 const refs=[...new Set(references.filter(ref=>ref.relation==='work_request'&&/^WR-[0-9]{1,12}$/.test(ref.ref)).map(ref=>ref.ref))];
 const readable=refs.length ? await client.query(`select w.ref,w.title from ops.work_request w
  where w.organization_tenant_id=$1 and $2::text is not null and w.ref=any($3::text[]) and ${readableWorkRequestSql('w')}`,
 [tenant,sponsor,refs]).catch(()=>({rows:[]})) : {rows:[]};
 const requests=new Map(readable.rows.map(row=>[row.ref,row]));
 for(const reference of references) if(reference.relation==='work_request' && requests.has(reference.ref)) {
  reference.state='resolved';reference.title=requests.get(reference.ref).title||reference.ref;reference.target={work_request:reference.ref};
 }
 return projected;
}
