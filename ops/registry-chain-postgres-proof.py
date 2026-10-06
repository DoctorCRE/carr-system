#!/usr/bin/env python3
"""Prove one successor publication on a disposable database and isolated source clone."""
from pathlib import Path
import sys,json,subprocess,tempfile,hashlib,shutil,importlib.util
from contextlib import contextmanager
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'ops'));sys.path.insert(0,str(root/'tools'))
from git_env import fixture_env
from integration_candidate import allocation_plan
import successor_generation as generation
repo=Path(tempfile.mkdtemp(prefix='arch-c1-successor-proof-'))/'repo';env=fixture_env()
def git(*args,cwd=repo):return subprocess.check_output(['git',*args],cwd=cwd,env=env,stderr=subprocess.DEVNULL,text=True).strip()
head=git('rev-parse','HEAD',cwd=root);git('clone','--quiet','--shared','--no-checkout',str(root),str(repo),cwd=root);git('switch','-qc','proof',head)
git('config','user.name','Registry proof');git('config','user.email','registry-proof@example.invalid')
changed=git('diff','--name-only',cwd=root).splitlines()
for path in changed:
    shutil.copyfile(root/path,repo/path)
if changed:
    git('add','--',*changed)
    message=repo/'.git/proof-message';message.write_text('Use current source for disposable proof\n')
    git('commit','-q','-F',str(message));head=git('rev-parse','HEAD')
git('update-ref','refs/remotes/origin/main',head)
(repo/'mcp-server/node_modules').symlink_to(root/'mcp-server/node_modules',target_is_directory=True)
old=json.loads((repo/'ops/config/scac-registry-chain.json').read_text());frontier=old['versions'][-1];tail=max(int(p.name[:4]) for p in (repo/'migrations').glob('*.sql'))
domain=repo/f'migrations/{tail+1:04d}_chain_fixture_domain.sql';domain.write_text('create table public.chain_fixture_domain(id integer);\n')
seal=repo/f'migrations/{tail+2:04d}_chain_fixture_scac_successor.sql'
plan=allocation_plan(repo,head,[domain.name,seal.name])
fixture=generation.disposable_database
snapshots: list[str]=[]
@contextmanager
def snapshot_fixture(source):
    with fixture(source) as (dsn,fixture_env,run):
        yield dsn,fixture_env,run
        candidate=source/'.git/generated-successor-snapshot.sql'
        run(['sh',source/'bin/schema-snapshot.sh','--from-disposable-local',dsn,'--output-candidate',candidate])
        assert new_version in candidate.read_text()
        snapshots.append(str(candidate))
new_version=f'scac-mutation-registry.v{frontier["number"]+1}'
with patch.object(generation,'disposable_database',snapshot_fixture):
    result=generation.regenerate(repo,plan,[domain],seal,repo/frontier['migration'])
new=json.loads((repo/'ops/config/scac-registry-chain.json').read_text())
assert new['versions'][:-1]==old['versions']
assert new['versions'][-1]['number']==frontier['number']+1
assert hashlib.sha256((repo/'mcp-server/src/scac-mutation-registry.current.generated.js').read_bytes()).hexdigest()==new['versions'][-1]['artifact_sha256']
assert not (repo/f'mcp-server/src/scac-mutation-registry.v{frontier["number"]+1}.generated.js').exists()
subprocess.run(['node','--input-type=module','-e',"import {checkRegistryChain} from './ops/registry-chain-check.mjs'; checkRegistryChain();"],cwd=repo,env=env,check=True)
git('add','--',str(domain.relative_to(repo)),str(seal.relative_to(repo)),
    'ops/config/scac-registry-chain.json','ops/config/scac-registry-source-inventory-fixtures.v1.json',
    'ops/config/scac-registry-full-entry-set-seals.json','mcp-server/src/scac-mutation-registry.current.generated.js')
message=repo/'.git/proof-message';message.write_text('Accept generated successor fixture\n')
git('commit','-q','-F',str(message));accepted=git('rev-parse','HEAD')
git('switch','-qc','proof-main',head)
(repo/'proof-main.txt').write_text('Unrelated main advance\n');git('add','--','proof-main.txt')
message.write_text('Advance main independently\n');git('commit','-q','-F',str(message))
main=git('rev-parse','HEAD');git('update-ref','refs/heads/main',main)
git('remote','set-url','origin',str(repo));git('switch','-q','proof')
spec=importlib.util.spec_from_file_location('rehome_proof',root/'ops/rehome-successor.py')
assert spec is not None and spec.loader is not None
rehome=importlib.util.module_from_spec(spec);spec.loader.exec_module(rehome)
with patch.object(generation,'disposable_database',snapshot_fixture):
    receipt=rehome.rehome(repo)
readback=json.loads(receipt.read_text())
assert readback['approved_sha']==accepted and readback['main_sha']==main
assert json.loads((repo/'ops/config/scac-registry-chain.json').read_text())['versions'][:-1]==old['versions']
assert (repo/domain.relative_to(repo)).read_text()=='create table public.chain_fixture_domain(id integer);\n'
print(json.dumps({'repo':str(repo),'version':result['version'],'historical_pins_preserved':len(old['versions']),
    'runtime_pin_verified':True,'sql_disposable_readback':True,'successor_snapshots':snapshots,'rehome_receipt':str(receipt)}))
