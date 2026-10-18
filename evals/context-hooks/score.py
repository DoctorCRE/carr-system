import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def observed(doc, tool):
    return any(re.fullmatch(row.get('matcher',''), tool) and
               any('rule-pack-preuse-reselection.py' in h.get('command','') for h in row.get('hooks',[]))
               for row in doc['PreToolUse'])


def score(expectations, baseline, candidate):
    base = json.loads(subprocess.run(['git','show',expectations['baseline_ref']+':ops/config/hooks.json'],
                                    cwd=ROOT,capture_output=True,text=True,check=True).stdout)
    current = json.loads((ROOT/'ops/config/hooks.json').read_text())
    cases = expectations['cases']
    for rows, config in ((baseline,base),(candidate,current)):
        if {r['case_id'] for r in rows} != set(cases):
            raise ValueError('incomplete event matrix')
        for row in rows:
            case = cases[row['case_id']]
            if row['fires'] != observed(config,case['tool']):
                raise ValueError('observations differ from source wiring')
    def grade(rows):
        test = [r for r in rows if r['split']=='test']
        return sum(r['fires']==cases[r['case_id']]['required'] for r in test)/len(test)
    b,c=grade(baseline),grade(candidate)
    oracle=[dict(r, fires=cases[r['case_id']]['required']) for r in candidate]
    null=[dict(r, fires=not cases[r['case_id']]['required']) for r in candidate]
    agreement=float(grade(candidate)==grade(candidate))
    return {'dimensions':{'event-route-wiring':{'baseline':{'score':b,'ci_low':b,'ci_high':b},
            'candidate':{'score':c,'ci_low':c,'ci_high':c},
            'delta':{'value':c-b,'ci_low':c-b,'ci_high':c-b}}},
            'controls':{'agreement':agreement,'oracle_pass_rate':grade(oracle),'null_pass_rate':grade(null)}}
