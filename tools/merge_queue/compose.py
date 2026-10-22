"""Durable preparation of independent PR branches, with no landing authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from git_env import scrubbed_env


class Compositions:
    def __init__(self, queue):
        self.q = queue
        self.children = {}

    def live(self, action):
        child = self.children.get(action['key'])
        if child is not None:
            return child.poll() is None
        rows = subprocess.run(['ps', '-axo', 'command='], capture_output=True, text=True,
                              check=True, timeout=15).stdout.splitlines()
        return any(str(self.q.root / 'tools/prepare_pr.py') in row
                   and str(action['receipt']) in row for row in rows)

    def poll(self):
        q = self.q
        for action in q.db.execute("SELECT * FROM actions WHERE kind='regen' AND expected_base='parallel' AND phase='issued'").fetchall():
            e = q.db.execute('SELECT * FROM entries WHERE repo=? AND pr=? AND approved=?',
                             (action['repo'], action['pr'], action['head'])).fetchone()
            try:
                if self.live(action):
                    if time.time() >= q.deadline('action:'+action['key'], 'regeneration'):
                        q.report(e, 'composition_timeout', 'Composer remains live past its deadline; inspect before retry', 'blocked')
                    continue
                child = self.children.pop(action['key'], None)
                if child is not None:
                    child.wait(timeout=5)
                p = q.pr(action['repo'], action['pr'])
                path = Path(action['receipt'])
                receipt = json.loads(path.read_text()) if path.exists() else None
                if receipt is not None:
                    prepared = receipt.get('prepared', '')
                    if (receipt != dict(head=action['head'], main=action['payload'], prepared=prepared,
                                        branch=p['head']['ref']) or len(prepared) != 40):
                        raise ValueError('Composition receipt does not bind the request')
                    if p['head']['sha'] == prepared:
                        q.fetch(action['repo'], prepared)
                        q.git(action['repo'], 'merge-base', '--is-ancestor', action['head'], prepared)
                        q.git(action['repo'], 'merge-base', '--is-ancestor', action['payload'], prepared)
                        with q.db:
                            q.db.execute("UPDATE actions SET phase='done' WHERE key=?", (action['key'],))
                            q.db.execute('UPDATE entries SET composition_base=? WHERE repo=? AND pr=? AND approved=?',
                                         (action['payload'], action['repo'], action['pr'], prepared))
                        continue
                if p['head']['sha'] != action['head']:
                    q.report(e, 'head_moved', 'Provider head differs from composition and receipt', 'superseded')
                    with q.db:
                        q.db.execute("UPDATE actions SET phase='done' WHERE key=?", (action['key'],))
                    continue
                # The worker is absent and its push cannot precede the receipt.
                # Re-read the ref as well as the API before permitting another push.
                refs = q.git(action['repo'], 'ls-remote', 'origin', 'refs/heads/'+p['head']['ref']).split()
                if not refs or refs[0] != action['head']:
                    continue
                conflicts = q.conflict(action['repo'], p)
                if conflicts:
                    q.report(e, 'conflict', 'Composition source conflict requires review', 'conflict')
                with q.db:
                    phase = 'exhausted' if action['attempts'] >= 4 or conflicts else 'planned'
                    q.db.execute('UPDATE actions SET phase=? WHERE key=?', (phase, action['key']))
                if not conflicts:
                    q.retry(e, 'Composer stopped before confirmed push; provider head unchanged')
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                q.entry_failed(e, exc)

    def prepare(self, e, p):
        q, repo, head = self.q, e['repo'], e['approved']
        if p['head'].get('repo', {}).get('full_name', repo) != repo:
            raise ValueError('Composition refuses fork branches')
        if q.composition_current(e, p):
            return False
        q.fetch(repo, head)
        main = q.git(repo, 'rev-parse', 'origin/main').strip()
        # A regenerated pin can be consumed after the daemon restarted.
        for a in q.db.execute("SELECT * FROM actions WHERE kind='regen' AND phase='done' AND repo=? AND pr=? ORDER BY rowid DESC", (repo, e['pr'])).fetchall():
            try:
                receipt = json.loads(Path(a['receipt']).read_text())
            except (OSError, ValueError, TypeError):
                continue
            if receipt == dict(head=a['head'], main=a['payload'], prepared=head, branch=p['head']['ref']):
                candidate = dict(e, composition_base=a['payload'])
                if q.composition_current(candidate, p):
                    with q.db:
                        q.db.execute('UPDATE entries SET composition_base=? WHERE id=?', (a['payload'], e['id']))
                    return False
        key = f'regen:{repo}:{e["pr"]}:{head}:{main}'
        receipt = q.state / 'compositions' / (repo.split('/')[-1]+f'-{e["pr"]}-{head}-{main}.json')
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with q.db:
            q.db.execute("INSERT OR IGNORE INTO actions(key,kind,repo,pr,head,payload,receipt,expected_base) VALUES(?,?,?,?,?,?,?,'parallel')",
                         (key, 'regen', repo, e['pr'], head, main, str(receipt)))
        a = q.db.execute('SELECT * FROM actions WHERE key=?', (key,)).fetchone()
        if a['phase'] == 'exhausted':
            raise ValueError('Composition attempts exhausted; inspect receipt before rearming')
        if a['phase'] != 'planned':
            q.report(e, 'composing', 'Waiting for exact-head composition receipt')
            return True
        remote = q.git(repo, 'remote', 'get-url', 'origin').strip()
        argv = [sys.executable, str(q.root / 'tools/prepare_pr.py'), '--remote', remote,
                '--branch', p['head']['ref'], '--head', head, '--main', main,
                '--pr', str(e['pr']), '--receipt', str(receipt)]
        if repo != 'jbookout/carr-system':
            argv += ['--repo', repo]
        q.check_cancelled()
        with q.db:
            q.db.execute("UPDATE actions SET phase='issued',attempts=attempts+1 WHERE key=?", (key,))
            q.deadline('action:'+key, 'regeneration')
        with receipt.with_suffix('.log').open('a') as log:
            self.children[key] = subprocess.Popen(argv, cwd=q.root, env=scrubbed_env(),
                                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                  start_new_session=True)
        q.report(e, 'composing', f'Preparing {head} with main {main}')
        return True
