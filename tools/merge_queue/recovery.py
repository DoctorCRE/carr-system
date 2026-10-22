"""Observe each speculative landing and recover red main through ordinary PRs."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import time

from prepare_pr import git
from work_queue import WorkQueue, default_path


class Recovery:
    def __init__(self, queue, bounds):
        self.q, self.bounds = queue, bounds
        queue.db.execute('''CREATE TABLE IF NOT EXISTS canaries (
            repo TEXT NOT NULL, culprit INTEGER NOT NULL, head TEXT NOT NULL, commit_sha TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'canary', branch TEXT, revert_head TEXT, revert_pr INTEGER,
            effect TEXT, started REAL NOT NULL, PRIMARY KEY(repo,commit_sha))''')
        queue.db.commit()

    def record(self, e, p):
        if not e['composition_base']:
            return
        with self.q.db:
            self.q.db.execute('INSERT OR IGNORE INTO canaries(repo,culprit,head,commit_sha,started) VALUES(?,?,?,?,?)',
                              (e['repo'], e['pr'], e['approved'], p['merge_commit_sha'], time.time()))

    def update(self, r, **fields):
        with self.q.db:
            self.q.db.execute('UPDATE canaries SET '+','.join(k+'=?' for k in fields)+' WHERE repo=? AND commit_sha=?',
                              (*fields.values(), r['repo'], r['commit_sha']))
            if fields.get('phase') == 'blocked':
                self.q.event(r['repo'], r['culprit'], 'canary_blocked',
                             'Recovery deadline expired; reconcile the culprit entry after provider and process readback')

    def tick(self):
        q = self.q
        for row in q.db.execute("SELECT * FROM canaries WHERE phase NOT IN ('done','blocked') ORDER BY started").fetchall():
            r = dict(row)
            try:
                with q.github_scope('canary:'+r['repo']+':'+r['commit_sha']):
                    self.step(r)
            except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                with q.db:
                    q.event(r['repo'], r['culprit'], 'recovery_waiting', str(exc))
                if time.time()-r['started'] >= self.bounds['ci']['seconds']:
                    self.update(r, phase='blocked')
        return {r['repo'] for r in q.db.execute("SELECT repo FROM canaries WHERE phase IN ('revert','requeue','blocked')")}

    def verdict(self, r):
        workflow = 'main-canary.yml' if r['repo'] == 'jbookout/carr-system' else 'ci.yml'
        runs = self.q.api_pages(f'repos/{r["repo"]}/actions/workflows/{workflow}/runs?head_sha={r["commit_sha"]}&event=push&per_page=100', 'workflow_runs')
        runs = [run for run in runs if run['head_sha'] == r['commit_sha'] and run['event'] == 'push']
        if not runs:
            return 'pending'
        run = max(runs, key=lambda x: (x['id'], x.get('run_attempt', 1)))
        if run['status'] != 'completed':
            return 'pending'
        return 'green' if run['conclusion'] == 'success' else 'red' if run['conclusion'] in ('failure','timed_out','action_required') else 'pending'

    def step(self, r):
        q = self.q
        if time.time()-r['started'] >= self.bounds['ci']['seconds']:
            self.update(r, phase='blocked')
            return
        if r['phase'] == 'canary':
            verdict = self.verdict(r)
            if verdict == 'green':
                self.update(r, phase='done')
                return
            if verdict != 'red':
                return
            self.update(r, phase='revert', started=time.time())
            r.update(phase='revert', started=time.time())
        if r['phase'] == 'revert':
            branch = r['branch'] or 'merge-queue/revert-'+r['commit_sha']
            head = r['revert_head'] or self.revert_commit(r, r['commit_sha'], branch)
            self.update(r, branch=branch, revert_head=head)
            r.update(branch=branch, revert_head=head)
            pr = self.ensure_pr(r, branch, head, f'Revert #{r["culprit"]} after red main canary')
            if pr is None:
                return
            if r['revert_pr'] is None:
                self.update(r, revert_pr=pr['number'], effect=None)
                r.update(revert_pr=pr['number'], effect=None)
            if pr.get('merged'):
                entry = q.enqueue(r['repo'], pr['number'], head, 'canary rollback', rearm=False)
                e = q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                q.confirmed_merge(e, pr)
                self.update(r, phase='requeue', effect=None)
                return
            if r['effect'] == 'merge':
                return
            if (pr['state'] != 'open' or pr.get('draft') or q.held(pr)
                    or pr['base']['ref'] != 'main' or pr['head']['sha'] != head):
                raise ValueError('Revert PR changed or held; no merge authorized')
            if not q.green(r['repo'], pr['number'], head):
                return
            current = q.pr(r['repo'], pr['number'])
            if (current['head']['sha'] != head or q.held(current) or current['state'] != 'open'
                    or current['base']['ref'] != 'main' or current.get('draft')):
                raise ValueError('Revert PR changed during required checks')
            self.update(r, effect='merge')
            q.gh('pr', 'merge', str(pr['number']), '-R', r['repo'], '--squash', '--match-head-commit', head)
        elif r['phase'] == 'requeue':
            reverted = q.pr(r['repo'], r['revert_pr'])
            if not reverted.get('merged') or reverted['head']['sha'] != r['revert_head']:
                raise ValueError('Revert receipt changed before requeue')
            branch = 'merge-queue/reapply-'+r['commit_sha']
            head = self.revert_commit(r, reverted['merge_commit_sha'], branch)
            pr = self.ensure_pr(r, branch, head, f'Reapply #{r["culprit"]} after canary repair review')
            if pr is None:
                return
            card = 'canary-requeue:'+r['repo']+':'+r['commit_sha']
            with WorkQueue(default_path()) as work:
                if not any(c['card_id'] == card for c in work.export()['items']):
                    work.enqueue(card, desk='codex', actor='merge-daemon',
                                 task=f'Review and repair reapply PR https://github.com/{r["repo"]}/pull/{pr["number"]}. '
                                      f'Original approved head {r["head"]}; main canary failed after #{r["culprit"]}. Do not merge without review.',
                                 prs=[dict(repo=r['repo'], number=pr['number'], head=head)])
            self.update(r, phase='done', effect=None)
            with q.db:
                q.event(r['repo'], r['culprit'], 'canary_requeued', f'Reverted through #{r["revert_pr"]}; reapply #{pr["number"]} queued for repair and review')

    def ensure_pr(self, r, branch, head, title):
        q = self.q
        owner = r['repo'].split('/')[0]
        prs = q.pages(f'repos/{r["repo"]}/pulls?state=all&head={owner}:{branch}&per_page=100')
        matches = [p for p in prs if p['head']['ref'] == branch and p['head']['sha'] == head and p['base']['ref'] == 'main']
        if len(matches) > 1:
            raise ValueError('Multiple recovery PRs match one intent')
        if any(p['head']['ref'] == branch and p not in matches for p in prs):
            raise ValueError('Recovery PR head or base differs from its recorded intent')
        if matches:
            return q.pr(r['repo'], matches[0]['number'])
        effect = 'create:'+branch
        if r['effect'] == effect:
            return None
        # A stable branch name lets restart reconcile a lost create response.
        self.update(r, effect=effect)
        body = q.state / ('recovery-'+r['commit_sha']+'.txt')
        body.write_text(f'{title}. Culprit merge {r["commit_sha"]}.\n\n'
                        'Ordinary PR, required CI and exact head guard.\n\n'
                        'Skills used: mattpocock-skills:tdd, poteto-mode, Prove It Works, codebase-design.\n')
        q.gh('pr', 'create', '-R', r['repo'], '--base', 'main', '--head', branch,
             '--title', title, '--body-file', str(body))
        return None

    def revert_commit(self, r, commit, branch):
        q, repo = self.q, r['repo']
        q.fetch(repo, commit)
        refs = q.git(repo, 'ls-remote', 'origin', 'refs/heads/'+branch).split()
        existing = refs[0] if refs else None
        main = q.git(repo, 'rev-parse', 'origin/main').strip()
        if existing:
            q.fetch(repo, existing)
            base = q.git(repo, 'rev-parse', existing+'^1').strip()
            q.git(repo, 'merge-base', '--is-ancestor', commit, base)
            q.git(repo, 'merge-base', '--is-ancestor', base, main)
        else:
            base = main
        mirror = q.state / 'repos' / (repo.split('/')[-1]+'.git')
        with tempfile.TemporaryDirectory(prefix='carr-canary-revert.') as temp:
            tree = Path(temp)
            git(tree, 'clone', '-q', '--no-checkout', '--', str(mirror), str(tree))
            git(tree, 'checkout', '-q', '-b', 'revert', base)
            git(tree, 'config', 'user.name', 'jbookout')
            git(tree, 'config', 'user.email', 'jbookout@users.noreply.github.com')
            git(tree, 'revert', '--no-edit', commit)
            head = git(tree, 'rev-parse', 'HEAD')
            if existing:
                if git(tree, 'rev-parse', head+'^{tree}') != git(tree, 'rev-parse', existing+'^{tree}'):
                    raise ValueError('Recovery branch does not match the actual inverse')
                return existing
            git(tree, 'push', '-q', 'origin', f'HEAD:refs/heads/{branch}')
        q.check_cancelled()
        q.git(repo, 'push', '--quiet', 'origin', f'refs/heads/{branch}:refs/heads/{branch}')
        return head
