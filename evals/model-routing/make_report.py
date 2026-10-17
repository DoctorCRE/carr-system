"""Measure a finite, offline Codex boot network-policy matrix."""
import hashlib
import json
import subprocess
from pathlib import Path
import score

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


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
    print(json.dumps(measured,indent=2))


if __name__=='__main__':
    main()
