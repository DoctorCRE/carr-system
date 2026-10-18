#!/usr/bin/env python3
# ci: selftest
from __future__ import annotations

import ast
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'video'))
from operatory_math import make_operatory_plan

EXPECTED_JOB = {'compName': 'CARR_OperatoryMath',
 'width': 1080,
 'height': 1080,
 'fps': 29.97,
 'shots': [{'src': 'operatory.mov',
            'dur': 5.0,
            'pushIn': 1.06,
            'line': 'Out of operatories? Run this math.'},
           {'type': 'stat',
            'kind': 'counter',
            'dur': 5.0,
            'title': 'One general operatory can produce',
            'counter': {'target': 350, 'prefix': '$', 'suffix': 'K/yr', 'delay': 0.7, 'dur': 1.6},
            'sub': 'industry benchmarks run $300K to $600K'},
           {'type': 'stat',
            'kind': 'counter',
            'dur': 4.5,
            'title': 'Add two operatories',
            'counter': {'target': 700,
                        'prefix': '+$',
                        'suffix': 'K/yr',
                        'delay': 0.6,
                        'dur': 1.4,
                        'orange': True},
            'sub': 'in added yearly production capacity'},
           {'src': 'reception.mov',
            'dur': 5.0,
            'pushIn': 1.07,
            'line': 'Relocation sounds expensive. Price the offsets first.'},
           {'type': 'stat',
            'kind': 'stack',
            'dur': 9.0,
            'title': 'The real cost, after concessions',
            'rows': [{'label': 'Buildout for the new space', 'value': '$500K', 'delay': 0.7},
                     {'label': 'Landlord TI allowance',
                      'value': '-$180K',
                      'delay': 2.4,
                      'orange': True},
                     {'label': 'Free rent during buildout',
                      'value': '-$60K',
                      'delay': 4.0,
                      'orange': True}],
            'net': {'label': 'Net cost',
                    'target': 260,
                    'prefix': '$',
                    'suffix': 'K',
                    'delay': 5.9}},
           {'type': 'stat',
            'kind': 'counter',
            'dur': 6.0,
            'title': 'At a 35 percent margin, two ops add',
            'counter': {'target': 245, 'prefix': '$', 'suffix': 'K/yr', 'delay': 0.7, 'dur': 1.6},
            'sub': 'net cost paid back in about 13 months'},
           {'src': 'operatory.mov',
            'dur': 5.0,
            'pushIn': 1.08,
            'line': 'The right space can pay for itself. Run your numbers first.'}],
 'endCard': {'dur': 4.5,
             'name': 'Joe Bookout',
             'title': 'HEALTHCARE REAL ESTATE | CARR',
             'tagline': 'Tenant and buyer representation only'},
 'outPath': 'raw.mov'}

EXPECTED_COMMAND = ['ffmpeg',
 '-y',
 '-nostdin',
 '-i',
 'raw.mov',
 '-i',
 'music.mp3',
 '-i',
 'sweep.mp3',
 '-i',
 'sweep.mp3',
 '-i',
 'sweep.mp3',
 '-i',
 'sweep.mp3',
 '-i',
 'sweep.mp3',
 '-i',
 'sweep.mp3',
 '-i',
 'click.mp3',
 '-i',
 'click.mp3',
 '-i',
 'click.mp3',
 '-i',
 'click.mp3',
 '-i',
 'click.mp3',
 '-i',
 'click.mp3',
 '-i',
 'click.mp3',
 '-i',
 'riser.mp3',
 '-i',
 'impact.mp3',
 '-filter_complex',
 '[1:a]atrim=0:44.0,loudnorm=I=-23:TP=-3:LRA=7,afade=t=in:d=0.7,afade=t=out:st=41.8:d=2.2[mus];[2:a]adelay=4880|4880,volume=0.3[s2];[3:a]adelay=9880|9880,volume=0.3[s3];[4:a]adelay=14380|14380,volume=0.3[s4];[5:a]adelay=19380|19380,volume=0.3[s5];[6:a]adelay=28380|28380,volume=0.3[s6];[7:a]adelay=34380|34380,volume=0.3[s7];[8:a]adelay=5700|5700,volume=0.45[s8];[9:a]adelay=10600|10600,volume=0.45[s9];[10:a]adelay=20200|20200,volume=0.45[s10];[11:a]adelay=21900|21900,volume=0.45[s11];[12:a]adelay=23500|23500,volume=0.45[s12];[13:a]adelay=25400|25400,volume=0.45[s13];[14:a]adelay=29200|29200,volume=0.45[s14];[15:a]adelay=37500|37500,volume=0.5[s15];[16:a]adelay=39380|39380,volume=0.7[s16];[mus][s2][s3][s4][s5][s6][s7][s8][s9][s10][s11][s12][s13][s14][s15][s16]amix=inputs=16:normalize=0,alimiter=limit=0.89[aout]',
 '-map',
 '0:v',
 '-map',
 '[aout]',
 '-c:v',
 'libx264',
 '-preset',
 'medium',
 '-crf',
 '18',
 '-pix_fmt',
 'yuv420p',
 '-movflags',
 '+faststart',
 '-c:a',
 'aac',
 '-b:a',
 '256k',
 '-ar',
 '48000',
 '-t',
 '44.0',
 'final.mp4']

plan = make_operatory_plan(operatory='operatory.mov', reception='reception.mov', raw_output='raw.mov')
assert json.loads(plan.job_json) == EXPECTED_JOB
assert plan.job_json == json.dumps(EXPECTED_JOB, indent=2)
assert plan.audio.total == 44.0
assert plan.audio.endcard_at == 39.5
assert plan.audio.sweeps == (4.88, 9.88, 14.38, 19.38, 28.38, 34.38)
assert plan.audio.clicks == (5.7, 10.6, 20.2, 21.9, 23.5, 25.4, 29.2)
try:
    setattr(plan.audio, 'total', 0.0)
except FrozenInstanceError:
    pass
else:
    raise AssertionError('audio schedule must be immutable')

tree = ast.parse((ROOT / 'video/build-operatory-math.py').read_text())
def assigns(node: ast.stmt, name: str) -> bool:
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == name for target in node.targets)
first = next(i for i, node in enumerate(tree.body) if assigns(node, 'inputs'))
last = next(i for i, node in enumerate(tree.body) if assigns(node, 'cmd'))
audio_nodes = tree.body[first:last + 1]
for statement in audio_nodes:
    for node in ast.walk(statement):
        assert not isinstance(node, (ast.Import, ast.ImportFrom))
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id in {'str', 'round', 'add', 'len'}
            elif isinstance(node.func, ast.Attribute):
                owner = node.func.value
                assert ((isinstance(owner, ast.Name) and
                         (owner.id, node.func.attr) in {
                             ('inputs', 'extend'), ('filters', 'append'), ('mix', 'append')}) or
                        (isinstance(owner, ast.Constant) and isinstance(owner.value, str) and
                         node.func.attr == 'join'))
            else:
                raise AssertionError('unexpected audio assembly call')
namespace = {'FF': 'ffmpeg', 'RAW': 'raw.mov', 'FINAL': 'final.mp4', 'MUSIC': 'music.mp3',
             'SWEEP': 'sweep.mp3', 'CLICK': 'click.mp3', 'RISER': 'riser.mp3', 'IMPACT': 'impact.mp3',
             'total': plan.audio.total, 'endcard_at': plan.audio.endcard_at,
             'sweeps': plan.audio.sweeps, 'clicks': plan.audio.clicks}
exec(compile(ast.Module(body=audio_nodes, type_ignores=[]), '<audio assembly>', 'exec'), namespace)
assert namespace['cmd'] == EXPECTED_COMMAND
print('operatory math: exact payload, immutable timing, and audio command passed')
