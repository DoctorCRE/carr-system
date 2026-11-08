// Exercise the proof CLI with a recorded migrated-database response.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync, execFileSync } from 'node:child_process';
const proof=fileURLToPath(new URL('../../ops/verify-subject-watch-delivery-proof.py',import.meta.url));
test('proof CLI passes exact scheduled evidence and refuses duplicates and insufficient evidence',t=>{
 const sample=JSON.parse(readFileSync(new URL('../contracts/subject-watch-scheduled-proof.v1.fixture.json',import.meta.url)));
 const root=mkdtempSync(join(tmpdir(),'watch-proof-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for (const [name,mutate,status] of [
  ['pass',()=>{},0],['duplicate',v=>v.sample.notifications=2,1],
  ['business mutation',v=>v.sample.business_mutations=1,1],
  ['missing retry',v=>v.sample.scheduled_at=v.sample.first_delivery_at,78],
  ['unknown release',v=>v.release.git_sha.value=null,78],
  ['older release',v=>v.release.git_sha.value='b'.repeat(40),78],
 ]) {
  const fixture=structuredClone(sample);mutate(fixture);const path=join(root,name+'.json');writeFileSync(path,JSON.stringify(fixture));
  const result=spawnSync(process.env.CARR_CI_PYTHON??'python3',[proof,'--fixture',path,'--expected-merge','a'.repeat(40),'--watch-id',sample.sample.watch_id],{encoding:'utf8'});
  assert.equal(result.status,status,name+' '+result.stdout+' '+result.stderr);
 }
});

test('release containment accepts descendants and rejects older and unrelated commits',t=>{
 const root=mkdtempSync(join(tmpdir(),'watch-proof-git-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 const git=(...args)=>execFileSync('git',['-C',root,...args],{encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
 git('init','--initial-branch=main');git('config','user.name','Synthetic proof');git('config','user.email','synthetic@example.test');
 const message=join(root,'message');writeFileSync(message,'synthetic proof commit');
 const commit=()=>{git('commit','--allow-empty','-F',message);return git('rev-parse','HEAD');};
 const older=commit(),expected=commit(),descendant=commit();
 git('switch','--orphan','unrelated');const unrelated=commit();
 const base=JSON.parse(readFileSync(new URL('../contracts/subject-watch-scheduled-proof.v1.fixture.json',import.meta.url)));
 for(const [observed,status] of [[expected,0],[descendant,0],[older,78],[unrelated,78]]) {
  const fixture=structuredClone(base);fixture.release.git_sha.value=observed;const path=join(root,'fixture.json');writeFileSync(path,JSON.stringify(fixture));
  const result=spawnSync(process.env.CARR_CI_PYTHON??'python3',[proof,'--fixture',path,'--expected-merge',expected,'--watch-id',fixture.sample.watch_id],{cwd:root,encoding:'utf8'});
  assert.equal(result.status,status,result.stdout+result.stderr);
 }
});
