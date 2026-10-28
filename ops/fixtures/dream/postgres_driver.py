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
report = dream.apply(store, root, notes, ['synthetic-a', 'synthetic-b', 'synthetic-c'], attach, 'synthetic-dream')
assert dream.apply(store, root, notes, ['synthetic-a', 'synthetic-b', 'synthetic-c'], attach, 'synthetic-dream') == report
print(json.dumps({'done': report}), flush=True)
