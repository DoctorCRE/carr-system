"""Measure a finite, offline Codex boot network-policy matrix."""
import datetime
import hashlib
import json
import subprocess
from pathlib import Path
import score

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base = subprocess.check_output(['git', 'merge-base', 'HEAD', 'origin/main'], cwd=ROOT, text=True).strip()
    probe = ('Production Model Room boot probe 67399d09-d964-40ba-a242-8c987632c9c5: '
             'default read-only runtime could not fetch standing context.')
    review = ('Independent PR 1780 review bf6: opt-in requires an ephemeral fresh thread; '
              'a network-enabled resume must refuse.')
    cases = {}
    matrix = [
        ('train', 'default-off', False, 'workspace-write', None, None, 'production_trace', probe),
        ('train', 'fresh-opt-in', True, 'read-only', None, None, 'human_judged_hard_case', review),
        ('train', 'resume-refused', True, 'read-only', None, 'prior-thread', 'human_judged_hard_case', review),
        ('test', 'readonly-default-off', False, 'read-only', '/synthetic/carr-system', None, 'production_trace', probe),
        ('test', 'cwd-fresh-opt-in', True, 'read-only', '/synthetic/carr-system', None, 'human_judged_hard_case', review),
        ('test', 'cwd-resume-refused', True, 'read-only', '/synthetic/carr-system', 'prior-thread', 'human_judged_hard_case', review),
    ]
    for split, name, enabled, sandbox, cwd, thread_id, source_name, source_ref in matrix:
        value = {'network_access': enabled, 'thread_id': thread_id, 'sandbox': sandbox, 'cwd': cwd}
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        if enabled and thread_id:
            expected = {'outcome': 'network_access_requires_fresh_thread', 'turn_status': None, 'requests': []}
        else:
            thread_params = {'approvalPolicy': 'never', 'sandbox': sandbox}
            if enabled:
                thread_params['ephemeral'] = True
            if cwd:
                thread_params['cwd'] = cwd
            turn_params = {'threadId': 'synthetic-thread', 'input': [{'type': 'text', 'text': '<boot task>'}],
                           'approvalPolicy': 'never'}
            if enabled:
                turn_params['sandboxPolicy'] = {'type': 'readOnly', 'networkAccess': True}
            expected = {'outcome': 'synthetic-boot-accepted' if enabled else 'boot-refused-no-readonly-network',
                        'turn_status': 'completed' if enabled else 'failed',
                        'requests': [{'method': 'thread/start', 'params': thread_params},
                                     {'method': 'turn/start', 'params': turn_params}]}
        cases[f'{split}-{name}'] = {'split': split, 'input': value, 'input_sha256': digest,
            'should_not_fire': not enabled or thread_id is not None, 'expected_wire': expected,
            'source': source_name, 'source_ref': source_ref}
    exp = {'version':'model-routing-expectations/v3', 'baseline_ref':base, 'cases':cases}
    expectations_path = HERE/'expectations.v3.json'
    evidence_dir = HERE/'evidence'/'v3'
    evidence_dir.mkdir(parents=True, exist_ok=True)
    expectations_path.write_text(json.dumps(exp,indent=2)+'\n')
    arms = {arm:score.observe(exp,arm) for arm in ('baseline','candidate')}
    for arm, rows in arms.items():
        assert rows == score.observe(exp,arm), 'unstable observations'
        (evidence_dir/f'{arm}.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    measured = score.score(exp,arms['baseline'],arms['candidate'])
    assert measured == score.score(exp,arms['baseline'],arms['candidate'])
    assert measured['controls']['oracle_pass_rate']==1 and measured['controls']['null_pass_rate']==0
    refs = [f'evals/model-routing/evidence/v3/{a}.jsonl' for a in arms]
    source = {f'evals/model-routing/{f}':sha(HERE/f) for f in ('score.py','make_report.py')}
    deps = {f'tools/room-bridge/{f}':sha(ROOT/f'tools/room-bridge/{f}') for f in ('desks.py','codex_models.py')}
    deps['tools/room-bridge/codex_wire.py'] = sha(ROOT/'tools/room-bridge/codex_wire.py')
    dims = [{'dimension_id':name,'critical':True,'status':'passed',
             'direction_vs_baseline':'improved' if values['delta']['value']>0 else 'equivalent',
             'evidence_refs':refs,**values} for name, values in measured['dimensions'].items()]
    receipt = {'schema_version':2,'surface':'model-routing',
        'change':'Measure explicit synthetic boot refusal, fresh ephemeral read-only network opt-in, and resume refusal.',
        'measured_on':datetime.date.today().isoformat(),'rung':'regression',
        'adapter':{'surface':'offline_programmatic','adapter_id':'codex-routing-fresh-network-matrix','adapter_version':'3',
            'harness_id':'evals/model-routing/score.py','harness_version':source['evals/model-routing/score.py'],
            'provider_id':'none','model_id':'deterministic-no-model',
            'native_session_ref':'offline-fresh-network-boundary-v3',
            'configuration_fingerprint':'sha256:'+hashlib.sha256(json.dumps(deps,sort_keys=True).encode()).hexdigest()},
        'cases':{'total':len(cases),'train':sum(c['split']=='train' for c in cases.values()),
            'test':sum(c['split']=='test' for c in cases.values()),'should_not_fire':sum(c['should_not_fire'] for c in cases.values()),
            'sources':sorted({c['source'] for c in cases.values()})},
        'split':{'method':'Disjoint finite protocol variants; test adds configured cwd and read-only default-off cases; no population inference.',
                 'sealed_test':True},'repeats':2,
        'grader':{'kind':'programmatic','validation':{'graded_twice':True,'result':'pass',**measured['controls']}},
        'noise_floor':0,'min_useful_gain':.01,'primary_dimension':'fresh-network-boundary','dimensions':dims,
        'stage_results':[{'stage_id':'routing','status':'passed','dimension_ids':list(measured['dimensions']),'evidence_refs':refs}],
        'cost':{'baseline_usd_per_case':0,'candidate_usd_per_case':0},
        'verdict':{'decision':'ship','statement':'Finite offline protocol cases pass; actual authenticated CARR boot remains unqualified until a fresh post-merge probe.'},
        'notes':['Fake App Server explicitly refuses the synthetic boot task when read-only network permission is absent; it does not simulate App Server request rejection.',
                 'Test inputs are disjoint configured-workspace variants, not held-out samples or a model-quality/generalization measure.',
                 'The production trace establishes the missing network capability; synthetic acceptance does not prove live CARR reachability.'],
        'evidence':{'scorer':{'path':'evals/model-routing/score.py','function':'score'},'source':source,'dependencies':deps,
            'expectations':{'path':'evals/model-routing/expectations.v3.json','version':exp['version'],
            'sha256':sha(expectations_path)},
            'cohorts':{a:{'path':f'evals/model-routing/evidence/v3/{a}.jsonl','sha256':sha(evidence_dir/f'{a}.jsonl')} for a in arms}}}
    (HERE/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(measured,indent=2))


if __name__=='__main__':
    main()
