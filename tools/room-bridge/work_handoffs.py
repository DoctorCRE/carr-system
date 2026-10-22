"""Advance authorized work from completion receipts, never from delivery notices."""
from __future__ import annotations

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from work_queue import QueueChanged

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('handoff_review_contract', ROOT / 'ops/release-pipeline.py')
assert spec is not None and spec.loader is not None
REVIEW = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REVIEW)
REVIEW_RULES = json.loads((ROOT / 'ops/config/release-pipeline.v1.json').read_text())['worker']
REPOS = {'jbookout/carr-system'}


def model_family(entry):
    kind = entry.get('kind', '')
    if kind.startswith('codex'):
        return 'openai'
    if kind.startswith('claude'):
        return 'anthropic'
    if kind == 'grok-cli':
        return 'xai'
    if kind == 'hermes-nous':
        return entry.get('family', '')
    return ''


def result_pr(result):
    pin = result.get('pr')
    if not isinstance(pin, dict):
        text = str(result.get('result', ''))
        trailers = re.findall(r'^CARR_WORK_RESULT (.+)$', text, re.M)
        if len(trailers) == 1:
            try:
                pin = json.loads(trailers[0])
            except ValueError:
                return None
        else:
            urls = set(re.findall(r'https://github.com/(jbookout/carr-system)/pull/([0-9]+)', text))
            heads = set(re.findall(r'(?i)\bhead(?: sha)?\s*[:=]?\s*`?([0-9a-f]{40})\b', text))
            if len(urls) != 1 or len(heads) != 1:
                return None
            repo, number = urls.pop()
            pin = {'repo': repo, 'number': int(number), 'head': heads.pop()}
    if not isinstance(pin, dict):
        return None
    if (pin.get('repo') not in REPOS or type(pin.get('number')) is not int or pin['number'] <= 0
            or not re.fullmatch('[0-9a-f]{40}', str(pin.get('head', '')))):
        return None
    return {key: pin[key] for key in ('repo', 'number', 'head')}


def main_merges_only(root, old, new, main):
    """The first-parent delta contains only merges whose other parents are on main."""
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True,
                                       stderr=subprocess.DEVNULL, timeout=60).strip()
    try:
        git('merge-base', '--is-ancestor', old, new)
        if git('log', '--no-merges', '--first-parent', '--format=%H', f'{old}..{new}'):
            return False
        merges = git('rev-list', '--first-parent', '--parents', f'{old}..{new}').splitlines()
        for commit in merges:
            parents = commit.split()[1:]
            if len(parents) < 2:
                return False
            for parent in parents[1:]:
                git('merge-base', '--is-ancestor', parent, main)
        return bool(merges)
    except (subprocess.SubprocessError, OSError):
        return False


class Handoffs:
    def __init__(self, queue, desks, provider):
        self.queue, self.desks, self.provider = queue, desks, provider

    def tick(self):
        if os.environ.get('CARR_WORK_AUTO_HANDOFFS') != '1':
            return
        desks = self.desks() if callable(self.desks) else self.desks
        for row in self.queue.handoff_candidates():
            try:
                self._advance(row, desks)
            except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                print(f"handoff deferred for {row['card_id']}: {type(exc).__name__}", file=sys.stderr)

    def _advance(self, row, desks):
        data = json.loads(row['data'])
        if row['stage'] == 'merging' and data.get('handoff', {}).get('approved_head'):
            self._repin(row, data, desks)
            return
        if row['stage'] != 'review':
            return
        completion = data.get('completion', {})
        result = data.get('last_result', {})
        if completion.get('stage') == 'review' and data.get('handoff'):
            self._review(row, data, result, desks)
            return
        if (result.get('status') != 'completed' or result.get('termination_confirmed') is False
                or completion.get('stage') not in ('building', 'fixing')):
            return
        pin = result_pr(result)
        if pin is None:
            return
        if completion['stage'] == 'fixing' and pin in data['prs']:
            builder = data['handoff']['builder']
            task = (f"no new commits for {pin['repo']} PR #{pin['number']} at {pin['head']}. "
                    f"Make the requested source fixes before returning.\nFindings: {json.dumps(data['findings'])}\n\n"
                    + builder['task'])
            self.queue.handoff(row, stage='fixing', desk=builder['desk'], task=task,
                               options=json.loads(row['options']), data=data, reason='no new commits')
            return
        self._queue_review(row, data, pin, desks, reason='builder completion queued independent review')

    def _queue_review(self, row, data, pin, desks, *, reason):
        completion = data.get('completion', {})
        result = data.get('last_result', {})
        builder = data.get('handoff', {}).get('builder') or {
            'desk': completion['desk'], 'task': row['task'],
            'options': json.loads(row['options']),
            'family': (model_family(result) or model_family({'kind': data.get('executor_kind', '')})
                       or model_family(desks.get(completion['desk'], {})))}
        if completion.get('stage') in ('building', 'fixing'):
            builder['family'] = model_family(result) or builder['family']
        reviewer = next((name for name, entry in sorted(desks.items())
                         if 'review' in name and entry.get('posture') != 'record-layer'
                         and entry.get('kind') in ('codex-session', 'codex-live', 'claude-api', 'grok-cli', 'hermes-nous')
                         and model_family(entry) and builder['family']
                         and model_family(entry) != builder['family']), None)
        if reviewer is None:
            return
        data['prs'] = [pin]
        data['ci_state'] = 'unknown'
        data['handoff'] = {'builder': builder, 'reviewer': reviewer}
        options = {'fresh': True, 'checkout': f"new:review-{pin['number']}-{pin['head'][:8]}",
                   'codex': desks[reviewer]['kind'].startswith('codex'),
                   'brief_kind': 'engineering'}
        for key in ('registry_path', 'result_path'):
            if key in builder['options']:
                options[key] = builder['options'][key]
        task = (HERE / 'briefs/reviewer.md').read_text().replace('__REPO__', pin['repo']).replace(
            '__PR__', str(pin['number'])).replace('__HEAD__', pin['head'])
        task += '\n' + (HERE / 'brief-footer.md').read_text()
        try:
            self.queue.handoff(row, stage='review', desk=reviewer, task=task, options=options,
                               data=data, reason=reason)
        except QueueChanged:
            return

    def _review(self, row, data, result, desks):
        if result.get('status') != 'completed' or result.get('termination_confirmed') is False:
            return
        pin = data['prs'][0]
        snapshot = self.provider.pr(pin)
        comments = [c for c in snapshot['comments'] if REVIEW.trusted_commenter(c, REVIEW_RULES)
                    and (REVIEW.verdict(c.get('body', ''), REVIEW_RULES)
                         or c.get('body', '').split('\n', 1)[0] == 'REQUEST_CHANGES')]
        if not comments or snapshot['state'] != 'open':
            return
        comment = max(comments, key=lambda c: (c.get('created_at', ''), c['id']))
        url = f"https://github.com/{pin['repo']}/pull/{pin['number']}#issuecomment-{comment['id']}"
        if url not in str(result.get('comment_url', '')) + str(result.get('result', '')):
            return
        started = data['handoff'].get('review_started_at')
        if not started or datetime.fromisoformat(comment['created_at']) < datetime.fromisoformat(started).replace(microsecond=0):
            return
        if snapshot['head'] != pin['head'] and not self.provider.main_merges_only(pin, snapshot['head']):
            self._queue_review(row, data, {**pin, 'head': snapshot['head']}, desks,
                               reason='source head changed; fresh review')
            return
        lines = comment['body'].splitlines()
        if lines[0] == 'REQUEST_CHANGES':
            if len(lines) < 2 or lines[1] != f"Reviewed-SHA: {pin['head']}":
                return
            if not snapshot.get('same_repo', True):
                return
            pin = {**pin, 'head': snapshot['head']}
            data['prs'] = [pin]
            builder = data['handoff']['builder']
            data['findings'].append({'id': f"comment-{comment['id']}", 'level': 'P1',
                                     'summary': '\n'.join(lines[2:]), 'resolved': False})
            task = (f"Fix findings for {url} at {pin['head']}.\n" + comment['body']
                    + '\n\n' + builder['task'])
            options = {**builder['options'], 'fresh': True, 'checkout': snapshot['branch']}
            capped = row['review_round'] >= 3
            try:
                self.queue.handoff(row, stage='blocked' if capped else 'fixing', desk=builder['desk'], task=task,
                                   options=options, data=data, reason='round cap' if capped else 'reviewer requested changes')
            except QueueChanged:
                pass
            return
        if REVIEW.reviewed_header_sha(comment['body']) != pin['head']:
            return
        reviewed = pin['head']
        if snapshot['head'] != reviewed:
            pin = {**pin, 'head': snapshot['head']}
            data['prs'] = [pin]
        data['ci_state'] = snapshot['ci_state']
        data['handoff']['approved_head'] = reviewed
        try:
            self.queue.handoff(row, stage='merging', desk=row['desk'], task=row['task'],
                               options=json.loads(row['options']), data=data,
                               reason='exact-head reviewer approval',
                               effect=lambda: self.provider.release_hold(pin))
        except QueueChanged:
            return

    def _repin(self, row, data, desks):
        pin = data['prs'][0]
        snapshot = self.provider.pr(pin)
        if snapshot['state'] != 'open' or snapshot['head'] == pin['head']:
            return
        if not self.provider.main_merges_only(pin, snapshot['head']):
            self._queue_review(row, data, {**pin, 'head': snapshot['head']}, desks,
                               reason='source head changed; fresh review')
            return
        data['prs'] = [{**pin, 'head': snapshot['head']}]
        data['ci_state'] = snapshot['ci_state']
        try:
            self.queue.handoff(row, stage='merging', desk=row['desk'], task=row['task'],
                               options=json.loads(row['options']), data=data,
                               reason='main-merge-only head re-pinned')
        except QueueChanged:
            return


class GitHub:
    """Provider readbacks and the one idempotent hold-label effect. Never merges."""
    def _gh(self, *args):
        time.sleep(2)
        result = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError('GitHub handoff readback failed')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def _pull(self, pin):
        return self._gh('api', f"repos/{pin['repo']}/pulls/{pin['number']}")

    def pr(self, pin):
        pull = self._pull(pin)
        pages = self._gh('api', '--paginate', '--slurp',
                         f"repos/{pin['repo']}/issues/{pin['number']}/comments?per_page=100")
        checks = self._gh('pr', 'view', str(pin['number']), '--repo', pin['repo'],
                          '--json', 'headRefOid,statusCheckRollup')
        if checks['headRefOid'] != pull['head']['sha']:
            raise QueueChanged('PR moved during handoff readback')
        outcomes = [str(c.get('conclusion') or c.get('state') or c.get('status') or '').upper()
                    for c in checks['statusCheckRollup']]
        if any(c in ('FAILURE', 'ERROR', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED') for c in outcomes):
            ci = 'failed'
        elif outcomes and all(c in ('SUCCESS', 'NEUTRAL', 'SKIPPED') for c in outcomes):
            ci = 'passed'
        else:
            ci = 'pending' if outcomes else 'unknown'
        return {'head': pull['head']['sha'], 'state': pull['state'], 'branch': pull['head']['ref'],
                'same_repo': pull['head']['repo']['full_name'] == pin['repo'], 'ci_state': ci,
                'labels': [label['name'] for label in pull['labels']],
                'comments': [comment for page in pages for comment in page]}

    def release_hold(self, pin):
        pull = self._pull(pin)
        if pull['state'] != 'open' or pull['head']['sha'] != pin['head']:
            raise QueueChanged('PR changed before hold release')
        if 'do_not_merge' not in [label['name'] for label in pull['labels']]:
            return
        self._gh('api', '--method', 'DELETE',
                 f"repos/{pin['repo']}/issues/{pin['number']}/labels/do_not_merge")
        after = self._pull(pin)
        if after['head']['sha'] != pin['head'] or any(label['name'] == 'do_not_merge' for label in after['labels']):
            raise QueueChanged('hold release not verified at pinned head')

    def main_merges_only(self, pin, head):
        if pin['repo'] not in REPOS or not re.fullmatch('[0-9a-f]{40}', head):
            return False
        with tempfile.TemporaryDirectory(prefix='work-handoff-') as tmp:
            def git(*args):
                subprocess.run(['git', *args], cwd=tmp, check=True, capture_output=True,
                               text=True, timeout=120)
            git('init', '--bare')
            git('fetch', '--no-tags', f"https://github.com/{pin['repo']}.git",
                pin['head'], head, 'refs/heads/main:refs/heads/main')
            return main_merges_only(tmp, pin['head'], head, 'main')
