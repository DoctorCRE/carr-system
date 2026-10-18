import hashlib
import json
import subprocess
from pathlib import Path
import score

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = 'b4dc32fd'


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
    print(json.dumps(measured, indent=2))


if __name__=='__main__':
    main()
