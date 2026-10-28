"""Drive the dream with a JSON-line doctrine adapter for the PostgreSQL proof."""
import importlib.util
import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('dream', repo / 'ops/dream.py')
dream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dream)


def call(verb, args):
    print(json.dumps({'verb': verb, 'args': args}), flush=True)
    result = json.loads(sys.stdin.readline())
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result


class Store:
    def read(self):
        return call('read-doctrine', {'document': 'team-memory'})

    def gate(self, item, attach):
        section = next((s for s in self.read()['sections'] if s['section_key'] == item['section_key']), None)
        args = {'document': 'team-memory', 'op': item['op'], 'attach_id': attach}
        if section:
            args['section_id'] = section['section_id']
        if item['op'] == 'write':
            args['body_text'] = item['body_text']
        return call('dry-run-doctrine-gates', args)['result'] == 'pass'

    def apply(self, items, attach, key):
        return call('change-doctrine-sections', {'document': 'team-memory', 'attach_id': attach,
                    'idempotency_key': key, 'items': items})

    def log(self, report, key):
        return call('write-doctrine-section', {'document': 'dreams-log', 'section_key': 'fixture',
                    'body_text': json.dumps(report), 'base_version': 0, 'idempotency_key': key + '-log'})


attach, root = sys.argv[1:]
store = Store()
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
