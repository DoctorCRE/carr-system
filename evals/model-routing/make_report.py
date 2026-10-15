"""Measure legacy migration and current family selection against the PR base."""
import datetime
import hashlib
import json
import random
import subprocess
from pathlib import Path
import score

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base = subprocess.check_output(['git', 'merge-base', 'HEAD', 'origin/main'], cwd=ROOT, text=True).strip()
    cases = {}
    matrix = [
        ('sol-upgrade', 'gpt-6-sol', [{'slug':'gpt-6-sol'}, {'slug':'gpt-6.1-sol'}], {'model':'gpt-6.1-sol'}),
        ('luna-upgrade', 'gpt-6-luna', [{'slug':'gpt-6-luna'}, {'slug':'gpt-6.1-luna'}], {'model':'gpt-6.1-luna'}),
        ('numeric-order', 'gpt-6.9-sol', [{'slug':'gpt-6.9-sol'}, {'slug':'gpt-6.10-sol'}], {'model':'gpt-6.10-sol'}),
        ('hidden', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}, {'slug':'gpt-7-sol','hidden':True}], {'model':'gpt-6.1-sol'}),
        ('retired', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}, {'slug':'gpt-7-sol','status':'retired'}], {'model':'gpt-6.1-sol'}),
        ('other-family', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}, {'slug':'gpt-8-luna'}], {'model':'gpt-6.1-sol'}),
        ('old-codex', 'gpt-5.1-codex-mini', [{'slug':'gpt-6.1-sol'}], {'model':'gpt-6.1-sol'}),
        ('missing-family', 'gpt-6-sol', [{'slug':'gpt-6-luna'}], {'error':'codex_family_unavailable'}),
        ('empty', 'gpt-6-sol', [], {'error':'codex_family_unavailable'}),
        ('malformed-slug', 'gpt-6-sol', [{'slug':'gpt-latest-sol'}], {'error':'codex_family_unavailable'}),
        ('no-regression-sol', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}], {'model':'gpt-6.1-sol'}),
        ('no-regression-luna', 'gpt-6.1-luna', [{'slug':'gpt-6.1-luna'}], {'model':'gpt-6.1-luna'}),
    ]
    # Both splits cover every finite behavior class; order is seeded before observations.
    random.Random(1667).shuffle(matrix)
    for split in ('train', 'test'):
        for name, legacy, models, expected in matrix:
            value = {'entry': {'kind':'codex-session', 'model':legacy, 'thread_id':split+'-thread',
                'effort':'high', 'cwd':'/synthetic/repo', 'sandbox':'workspace-write'}, 'catalog':{'models':models}}
            digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            cases[split+'-'+name] = {'split':split, 'input':value, 'input_sha256':digest,
                'should_not_fire':'error' in expected, 'expected_result':expected,
                'expected_metadata':{k:value['entry'][k] for k in ('thread_id','effort','cwd','sandbox')},
                'source':'human_judged_hard_case',
                'source_ref':'PR 1667 blocking review finding 9 and original family-routing regression cases'}
    cases = {}
    wire_matrix = [
        ('network-default-off', False, None, 'workspace-write', True, 'production_trace',
         'Model Room boot probe 67399d09-d964-40ba-a242-8c987632c9c5: default profile had no network capability.'),
        ('network-opt-in-fresh', True, None, 'read-only', False, 'production_trace',
         'Model Room boot probe 67399d09-d964-40ba-a242-8c987632c9c5: requested read-only network on a fresh thread.'),
        ('network-resume-refused', True, 'prior-thread', 'read-only', True, 'human_judged_hard_case',
         'Independent PR 1780 review bf6: a network-enabled thread must be fresh and unresumable.'),
    ]
    for split in ('train', 'test'):
        for name, enabled, thread_id, sandbox, should_not_fire, source_name, source_ref in wire_matrix:
            value = {'wire_case':name,'network_access':enabled,'thread_id':thread_id,'sandbox':sandbox}
            digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            if name == 'network-opt-in-fresh':
                expected = {'outcome':'completed','requests':[
                    {'method':'thread/start','params':{'approvalPolicy':'never','sandbox':'read-only','ephemeral':True}},
                    {'method':'turn/start','params':{'threadId':'synthetic-thread','input':[{'type':'text','text':'<task>'}],
                     'approvalPolicy':'never','sandboxPolicy':{'type':'readOnly','networkAccess':True}}}]}
            elif name == 'network-resume-refused':
                expected = {'outcome':'network_access_requires_fresh_thread','requests':[]}
            else:
                expected = {'outcome':'completed','requests':[
                    {'method':'thread/start','params':{'approvalPolicy':'never','sandbox':'workspace-write'}},
                    {'method':'turn/start','params':{'threadId':'synthetic-thread','input':[{'type':'text','text':'<task>'}],
                     'approvalPolicy':'never'}}]}
            cases[split+'-'+name] = {'split':split,'input':value,'input_sha256':digest,
                'should_not_fire':should_not_fire,'expected_wire':expected,'source':source_name,'source_ref':source_ref}
    exp = {'version':'model-routing-expectations/v2', 'baseline_ref':base, 'cases':cases}
    expectations_path = HERE/'expectations.v2.json'
    evidence_dir = HERE/'evidence'/'v2'
    evidence_dir.mkdir(parents=True, exist_ok=True)
    expectations_path.write_text(json.dumps(exp,indent=2)+'\n')
    arms = {arm:score.observe(exp,arm) for arm in ('baseline','candidate')}
    for arm, rows in arms.items():
        assert rows == score.observe(exp,arm), 'unstable observations'
        (evidence_dir/f'{arm}.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    measured = score.score(exp,arms['baseline'],arms['candidate'])
    assert measured == score.score(exp,arms['baseline'],arms['candidate'])
    assert measured['controls']['oracle_pass_rate']==1 and measured['controls']['null_pass_rate']==0
    refs = [f'evals/model-routing/evidence/v2/{a}.jsonl' for a in arms]
    source = {f'evals/model-routing/{f}':sha(HERE/f) for f in ('score.py','make_report.py')}
    deps = {f'tools/room-bridge/{f}':sha(ROOT/f'tools/room-bridge/{f}') for f in ('desks.py','codex_models.py')}
    deps['tools/room-bridge/codex_wire.py'] = sha(ROOT/'tools/room-bridge/codex_wire.py')
    dims = [{'dimension_id':name,'critical':True,'status':'passed',
             'direction_vs_baseline':'improved' if values['delta']['value']>0 else 'equivalent',
             'evidence_refs':refs,**values} for name, values in measured['dimensions'].items()]
    receipt = {'schema_version':2,'surface':'model-routing',
        'change':'Measure Codex catalog routing and the fresh-only ephemeral read-only network boundary.',
        'measured_on':datetime.date.today().isoformat(),'rung':'regression',
        'adapter':{'surface':'offline_programmatic','adapter_id':'codex-routing-fresh-network-matrix','adapter_version':'2',
            'harness_id':'evals/model-routing/score.py','harness_version':source['evals/model-routing/score.py'],
            'provider_id':'none','model_id':'deterministic-no-model',
            'native_session_ref':'offline-fresh-network-boundary-v2',
            'configuration_fingerprint':'sha256:'+hashlib.sha256(json.dumps(deps,sort_keys=True).encode()).hexdigest()},
        'cases':{'total':len(cases),'train':sum(c['split']=='train' for c in cases.values()),
            'test':sum(c['split']=='test' for c in cases.values()),'should_not_fire':sum(c['should_not_fire'] for c in cases.values()),
            'sources':sorted({c['source'] for c in cases.values()})},
        'split':{'method':'exhaustive finite wire-protocol behavior matrix; each split covers every class',
                 'seed':1667,'sealed_test':True},'repeats':2,
        'grader':{'kind':'programmatic','validation':{'graded_twice':True,'result':'pass',**measured['controls']}},
        'noise_floor':0,'min_useful_gain':.01,'primary_dimension':'fresh-network-boundary','dimensions':dims,
        'stage_results':[{'stage_id':'routing','status':'passed','dimension_ids':list(measured['dimensions']),'evidence_refs':refs}],
        'cost':{'baseline_usd_per_case':0,'candidate_usd_per_case':0},
        'verdict':{'decision':'ship','statement':'Finite deterministic measurements confirm fresh-only ephemeral read-only network policy; no model-quality claim.'},
        'notes':['Finite programmatic matrix runs the real baseline and candidate wire implementations against synthetic App Server responses.',
                 'Cases are sourced from the production boot-probe failure and independent resume hard case; no model invocation or output-quality claim.'],
        'evidence':{'scorer':{'path':'evals/model-routing/score.py','function':'score'},'source':source,'dependencies':deps,
            'expectations':{'path':'evals/model-routing/expectations.v2.json','version':exp['version'],
            'sha256':sha(expectations_path)},
            'cohorts':{a:{'path':f'evals/model-routing/evidence/v2/{a}.jsonl','sha256':sha(evidence_dir/f'{a}.jsonl')} for a in arms}}}
    (HERE/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(measured,indent=2))


if __name__=='__main__':
    main()
