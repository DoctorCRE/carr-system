# Main guard subprocess oracle

The files listed in `probes.json` are byte-identical to their Git blobs at its
`revision`, the merge base used for PR 2903. The fixture includes every local
Python import, including lazy imports and the filename-loaded record-home gate,
its AST-read exporter source and sibling constants, and the corpus policy manifest. It runs
through stdin JSON and process exit codes, using the same runner and fixture
environment as head. It never overlays head functions or imports head helpers.

`python3 ops/guard-selftest.py` checks byte identity before replaying the corpus.
The pinned revision must be available locally. CI checks out full history.
Hook verdicts are cached only for identical payloads during the immutable corpus
replay. The dirty-repository restore checks run separately without caching.
Four worker threads bound concurrent subprocesses and keep the oracle within
the gates-class timeout. They do not run commands contained in the payloads.

To refresh after an intentional baseline change, update `revision` to the full
reviewed merge-base SHA, then run this from the repository root. Preserve
`reviewer_probes` and `contract_overrides` when updating the manifest. Inspect
local imports at the new revision and update `files` if dependencies changed.

```python
import json
import pathlib
import subprocess

root = pathlib.Path('ops/fixtures/guard-main')
manifest = json.loads((root / 'probes.json').read_text())
for path in manifest['files']:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subprocess.check_output(
        ['git', 'show', f"{manifest['revision']}:{path}"]))
```

Run the selftest and relevant CI classes after refresh. Review byte changes as
baseline policy changes. Do not edit fixture source to silence a failing probe.
The zsh alternation probe records a pre-existing allowance in both versions;
it does not claim the rm glob parsing gap is fixed.
