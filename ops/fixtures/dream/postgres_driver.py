"""Drive the dream with a JSON-line doctrine adapter for the PostgreSQL proof."""
import importlib.util
import json
import sys
import subprocess
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('dream', repo / 'ops/dream.py')
assert spec and spec.loader
dream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dream)


def call(verb, args):
    print(json.dumps({'verb': verb, 'args': args}), flush=True)
    result = json.loads(sys.stdin.readline())
    if 'error' in result:
        return subprocess.CompletedProcess([], 1, '', 'TOOL ERROR ' + json.dumps(result))
    return subprocess.CompletedProcess([], 0, json.dumps(result), '')


attach, root = sys.argv[1:]
store = dream.VerbStore(call=call)
root = dream.clone(store, Path(root))
for i in range(1, 5):
    (root / f'duplicate/{i}.md').unlink()
(root / 'stale.md').unlink()
confirmed = root / 'confirmed.md'
confirmed.write_text(confirmed.read_text() + '\nVerified from session synthetic-a.\n')
notes = [{'sections': [f'duplicate/{i}'], 'sessions': ['synthetic-a'], 'note': 'duplicate'} for i in range(1, 5)]
notes += [{'sections': ['stale'], 'sessions': ['synthetic-b'], 'note': 'obsolete'},
          {'sections': ['confirmed'], 'sessions': ['synthetic-c'], 'note': 'confirmed'}]
observations = {}
for session, text in [('synthetic-a', 'duplicate'), ('synthetic-b', 'obsolete'),
                      ('synthetic-c', 'Verified from session synthetic-a.')]:
    transcript = root.parent / (session + '.jsonl')
    transcript.write_text(json.dumps({'message': {'role': 'user', 'content': text}}))
    observations[session] = dream.condense(transcript)
for unit in notes:
    section = unit['sections'][0]
    session = unit['sessions'][0]
    unit['claims'] = [{'section_key': section,
                       'text': 'Verified from session synthetic-a.' if section == 'confirmed' else unit['note'],
                       'label': 'quoted', 'anchors': [observations[session][0]['anchor']],
                       'supersedes': {'section_key': section, 'version': 1}}]
report = dream.apply(store, root, notes, observations, attach, 'synthetic-dream')
assert dream.apply(store, root, notes, observations, attach, 'synthetic-dream') == report
print(json.dumps({'done': report}), flush=True)
