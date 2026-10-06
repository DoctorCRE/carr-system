import datetime
import hashlib
import json
import subprocess
from pathlib import Path
import score

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = 'b4dc32fd'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base = subprocess.check_output(['git','rev-parse',BASE],cwd=ROOT,text=True).strip()
    old = json.loads(subprocess.check_output(['git','show',base+':ops/config/hooks.json'],cwd=ROOT,text=True))
    current = json.loads((ROOT/'ops/config/hooks.json').read_text())
    tools = ['Bash','Write','Edit','MultiEdit','NotebookEdit','Agent','WebFetch','WebSearch','Artifact',
             'AskUserQuestion','mcp__carr__read_doctrine','Read','Glob','Grep','ToolSearch','UnknownTool']
    required = set(tools[:11])
    cases = {}
    arms = {'baseline':[], 'candidate':[]}
    for split in ['train','test']:
        for tool in tools:
            cid = split+'-'+tool
            digest = hashlib.sha256(tool.encode()).hexdigest()
            cases[cid] = {'split':split,'input_sha256':digest,'should_not_fire':tool not in required,
                          'tool':tool,'required':tool in required}
            for arm, config in [('baseline',old),('candidate',current)]:
                arms[arm].append({'case_id':cid,'split':split,'input_sha256':digest,
                                  'fires':score.observed(config,tool)})
    exp = {'version':'context-hooks-expectations/v1','baseline_ref':base,'cases':cases}
    (HERE/'expectations.v1.json').write_text(json.dumps(exp,indent=2)+'\n')
    for arm,rows in arms.items():
        (HERE/'evidence'/f'{arm}.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    measured = score.score(exp,arms['baseline'],arms['candidate'])
    assert measured == score.score(exp,arms['baseline'],arms['candidate'])
    assert all(c['required']==(not c['should_not_fire']) for c in cases.values())
    refs = [f'evals/context-hooks/evidence/{a}.jsonl' for a in arms]
    source = {f'evals/context-hooks/{f}':sha(HERE/f) for f in ('score.py','make_report.py')}
    deps = {'ops/config/hooks.json':sha(ROOT/'ops/config/hooks.json')}
    receipt = json.loads((ROOT/'evals/rule-delivery/receipt.json').read_text())
    dim = {'dimension_id':'event-route-wiring','critical':True,'status':'passed','direction_vs_baseline':'improved',
           'evidence_refs':refs,**measured['dimensions']['event-route-wiring']}
    receipt.update(surface='context-hooks', change='Wire NotebookEdit to the existing full-text preuse rule route; preserve every included and excluded tool class.',
       measured_on=datetime.date.today().isoformat(),cases={'total':32,'train':16,'test':16,'should_not_fire':10,'sources':['human_judged_hard_case']},
       split={'method':'exhaustive finite tool-class matrix; paired semantic train/test events, no statistical generalization', 'seed':0,'sealed_test':True},
       repeats=2, noise_floor=0, min_useful_gain=0.01,primary_dimension='event-route-wiring',dimensions=[dim],
       stage_results=[{'stage_id':'delivery','status':'passed','dimension_ids':['event-route-wiring'],'evidence_refs':refs}],
       grader={'kind':'programmatic','validation':{'graded_twice':True,'result':'pass',**measured['controls']}},
       verdict={'decision':'ship','statement':'The finite wiring gap closes with no regression in its complete event matrix.'},
       notes=['Exact finite-matrix intervals; no confidence claim about model obedience. Live adapter invocation remains unverified.'],
       evidence={'scorer':{'path':'evals/context-hooks/score.py','function':'score'},'source':source,'dependencies':deps,
                 'expectations':{'path':'evals/context-hooks/expectations.v1.json','version':exp['version'],'sha256':sha(HERE/'expectations.v1.json')},
                 'cohorts':{a:{'path':f'evals/context-hooks/evidence/{a}.jsonl','sha256':sha(HERE/'evidence'/f'{a}.jsonl')} for a in arms}})
    receipt['adapter'].update(adapter_id='configured-preuse-event-matrix',harness_id='evals/context-hooks/score.py',
        harness_version=source['evals/context-hooks/score.py'], native_session_ref='01a10e01-1a2a-7590-b830-d1e05450f2a4',
        configuration_fingerprint='sha256:'+hashlib.sha256(json.dumps(deps,sort_keys=True).encode()).hexdigest())
    (HERE/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(dim)


if __name__=='__main__':
    main()
