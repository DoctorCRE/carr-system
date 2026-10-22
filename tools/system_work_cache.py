"""Cached GitHub census for the existing progress-board publisher, never a page read.

Read-only GitHub operations. The cache is projection data in board_snapshot;
source identity and suggestions do not grant branch or pull-request authority.
"""
import json
import copy
import time
import fcntl
import os
import tempfile
import re
import subprocess
import shutil
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from pathlib import Path

# Published identities stay stable across repository transfers.
REPOSITORIES = {"jbookout/carr-system": "DoctorCRE/carr-system",
                "jbookout/doctorcre-app": "jbookout/doctorcre-app"}
GIT_MIN_FREE_BYTES = 512 * 1024 * 1024
GIT_PACK_LIMIT_BYTES = 128 * 1024 * 1024
LIMITED_GIT = """import os, resource, sys
limit = int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
os.execvp(sys.argv[2], sys.argv[2:])
"""


def github_json(args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def dot_suggestions(report):
    """Parse only the report's branch headings; prose stays in its private record."""
    previous = ""
    section = None
    result = {}
    for line in report.splitlines():
        if line.startswith('ALREADY LANDED ANOTHER WAY'): section = 'cancel'
        elif line.startswith('SUPERSEDED') or 'Superseded, remaining' in line: section = 'cancel'
        elif 'Abandoned work worth finishing' in line: section = 'progress'
        elif 'Abandoned work to cancel' in line: section = 'cancel'
        if line.startswith('Last commit:'):
            name = previous.strip()
            if section and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_.-]+', name):
                result[name] = {'action': section, 'label': "The Dot's suggestion", 'source': 'Dot job13 report G, 2026-10-01'}
        previous = line
    return result


PR_FIELDS = "number title url headRefName createdAt updatedAt mergedAt state body"
PR_QUERY = """query($owner: String!, $name: String!, $history: String, $updates: String) {
  repository(owner: $owner, name: $name) {
    history: pullRequests(first: 100, after: $history,
      orderBy: {field: CREATED_AT, direction: ASC}) {
      nodes { %s } pageInfo { hasNextPage endCursor }
    }
    updates: pullRequests(first: 100, after: $updates,
      orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { %s } pageInfo { hasNextPage endCursor }
    }
  }
}""" % (PR_FIELDS, PR_FIELDS)


def pull_requests(repo, now, previous, read):
    state = copy.deepcopy(previous or {})
    owner, name = REPOSITORIES[repo].split('/')
    result = read(['api', 'graphql', '-f', 'query=' + PR_QUERY,
                   '-f', 'owner=' + owner, '-f', 'name=' + name,
                   '-F', 'history=' + (state.get('history') or 'null'),
                   '-F', 'updates=' + (state.get('updates') or 'null')])
    if result.get('errors'): raise ValueError('GraphQL errors')
    data = result['data']['repository']
    rows = state.setdefault('prs', {})
    # GitHub updates have second resolution; overlap the previous scan so
    # changes during its first second cannot fall behind a fractional cursor.
    watermark = date_value(state.setdefault('watermark', now.isoformat(timespec='seconds')))
    watermark = watermark.replace(microsecond=0) - timedelta(seconds=1)
    state.setdefault('scan_started', now.isoformat(timespec='seconds'))
    for stream in ('history', 'updates'):
        connection = data[stream]
        page = connection['pageInfo']
        nodes = connection['nodes']
        if not isinstance(nodes, list) or type(page['hasNextPage']) is not bool:
            raise ValueError('GraphQL page invalid')
        if page['hasNextPage'] and not nodes:
            raise ValueError('GraphQL nonterminal page is empty')
        cursor = page['endCursor']
        if nodes or page['hasNextPage']: required_text(cursor)
        if page['hasNextPage'] and cursor == state.get(stream):
            raise ValueError('GraphQL cursor did not advance')
        for pr in nodes:
            if type(pr['number']) is not int or pr['number'] < 1: raise ValueError('PR number invalid')
            for key in ('title', 'url', 'headRefName'): required_text(pr[key])
            date_value(pr['createdAt']); date_value(pr['updatedAt'])
            if pr['mergedAt'] is not None: date_value(pr['mergedAt'])
            if pr['state'] not in ('OPEN', 'CLOSED', 'MERGED'): raise ValueError('PR state invalid')
            # Retain only the fields needed by the public inventory, never PR prose.
            row = {key: pr[key] for key in ('number', 'title', 'url', 'headRefName',
                                          'createdAt', 'updatedAt', 'mergedAt', 'state')}
            row['work_refs'] = sorted(set(re.findall(r'WR-\d+', pr.get('body') or '')))
            old = rows.get(str(pr['number']))
            if old is None or date_value(old['updatedAt']) <= date_value(row['updatedAt']):
                rows[str(pr['number'])] = row
        if stream == 'history':
            state['history'] = cursor or state.get('history')
            state['history_complete'] = not page['hasNextPage']
        else:
            caught_up = not page['hasNextPage'] or any(
                date_value(pr['updatedAt']) < watermark for pr in nodes)
            state['updates_complete'] = caught_up
            state['updates'] = None if caught_up else cursor
            if caught_up:
                state['watermark'] = state.pop('scan_started')
    return sorted(rows.values(), key=lambda pr: (pr['createdAt'], pr['number']), reverse=True), state


def local_branches(repo, root, now):
    """Compare commits in a disposable bare repository, never a fleet checkout."""
    if any(os.environ.get(key) for key in ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE',
                                         'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_SHALLOW_FILE')):
        raise ValueError('inherited git repository routing is unsafe for the inventory fetch')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    name = repo.split('/')[1]
    with (root / (name + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        scratch = root / (name + '.scratch')
        marker = scratch / '.inventory-owner'
        if scratch.exists() or scratch.is_symlink():
            if scratch.is_symlink() or marker.is_symlink() or not marker.is_file() or marker.read_text() != 'system-work-cache.v1\n':
                raise ValueError('inventory scratch directory is not owned by this collector')
            shutil.rmtree(scratch)
        if shutil.disk_usage(root).free < GIT_MIN_FREE_BYTES:
            raise OSError('insufficient space for the inventory git fetch')
        scratch.mkdir()
        marker.write_text('system-work-cache.v1\n')
        try:
            mirror = scratch / 'repo.git'
            def git(*args):
                command = ['git', '--git-dir=' + str(mirror), *args]
                if args[0] == 'fetch':
                    command = [sys.executable, '-c', LIMITED_GIT, str(GIT_PACK_LIMIT_BYTES), *command]
                return subprocess.run(command,
                    capture_output=True, text=True, timeout=120, check=True).stdout.strip()
            subprocess.run(['git', 'init', '--bare', str(mirror)], capture_output=True,
                           text=True, timeout=30, check=True)
            git('fetch', '--no-tags', '--no-auto-maintenance', '--filter=tree:0',
                'https://github.com/' + REPOSITORIES[repo] + '.git', '+refs/heads/*:refs/heads/*')
            main = git('rev-parse', 'refs/heads/main')
            rows = []
            refs = git('for-each-ref', '--sort=refname', '--format=%(refname:strip=2)\t%(objectname)\t%(committerdate:iso-strict)', 'refs/heads/')
            for line in refs.splitlines():
                name, sha, date = line.split('\t')
                if name == 'main' or now - date_value(date) <= timedelta(days=7): continue
                base = git('merge-base', main, sha)
                if int(git('rev-list', '--count', base + '..' + sha)) == 0: continue
                # REST uses UTC Z dates; preserve the published bytes.
                date = date_value(date).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                rows.append({'name': name, 'sha': sha, 'date': date})
            return rows
        finally:
            shutil.rmtree(scratch)


def date_value(value):
    if not isinstance(value, str):
        raise ValueError('date missing')
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('date must include timezone')
    return date


def required_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('text missing')
    return value


def valid_cache(value):
    return (isinstance(value, dict) and value.get('schema') == 'system-work-external.v1'
            and isinstance(value.get('items'), list) and all(isinstance(row, dict) for row in value['items'])
            and isinstance(value.get('coverage'), list) and isinstance(value.get('complete'), bool))


def collect_github(now, read=github_json, suggestions=None, *, state=None, git_root=None, branches=local_branches):
    items, coverage, pr_heads, pr_work_refs = [], [], [], set()
    state = state if state is not None else {}
    git_root = git_root or Path(__file__).resolve().parents[1] / 'out/boards/system-work-git'
    for index, repo in enumerate(REPOSITORIES):
        try:
            if index and read is github_json: time.sleep(2)
            prs, next_state = pull_requests(repo, now, state.get(repo), read)
            for pr in prs:
                pr_heads.append({'repository':repo,'branch':pr['headRefName']})
                pr_work_refs.update(pr['work_refs'])
                completed = bool(pr['mergedAt'])
                if pr['state'] != 'OPEN' and not completed: continue
                items.append({'id': f"{repo}:pr:{pr['number']}", 'kind':'pull_request','title':pr['title'],
                    'state':'merged' if completed else 'open', 'opened_at':pr['createdAt'],
                    'last_activity_at':pr['mergedAt'] or pr['updatedAt'], 'owner':None,'version':None,
                    'completed':completed,'cancelled':False,'link':pr['url'],
                    'branch':pr['headRefName'],'work_refs':pr['work_refs'],'identity':{}})
            for branch in branches(repo, git_root, now):
                name, date, sha = branch['name'], branch['date'], branch['sha']
                items.append({'id':f'{repo}:branch:{name}','kind':'remote_branch','title':name,'state':'unmerged',
                    'opened_at':date,'last_activity_at':date,'owner':None,'version':sha,
                    'completed':False,'cancelled':False,'link':f'https://github.com/{REPOSITORIES[repo]}/tree/{quote(name,safe="")}',
                    'identity':{},'suggested_triage':(suggestions or {}).get(name) if repo.endswith('/carr-system') else None})
            state[repo] = next_state
            complete = next_state['history_complete'] and next_state['updates_complete']
            coverage.append({'repository':repo,'complete':complete,'reason':None if complete else 'github_history_pending'})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
            coverage.append({'repository':repo,'complete':False,'reason':'github_read_failed'})
    return {'schema':'system-work-external.v1','observed_at':now.isoformat(),
            'complete':all(c['complete'] for c in coverage),'completed_pr_history':True,'coverage':coverage,'pr_heads':pr_heads,'pr_work_refs':sorted(pr_work_refs),'items':items}


def unfinished_briefs(root, pr_heads):
    """Unstructured local briefs: include only explicit builders in either code home.

    Relationship gaps remain partial; no absence of an associated PR is proof of
    completion. No brief body is published into the cache.
    """
    rows=[]
    for path in sorted(Path(root).rglob('*brief*')):
        if not path.is_file() or path.suffix not in ('.md','.txt','.json'): continue
        text=path.read_text(errors='replace')
        if not re.search(r'(?im)^ROLE:\s*builder\b',text): continue
        repos=[repo for repo in REPOSITORIES if repo.split('/')[1] in text]
        if not repos: continue
        if re.search(r'(?:/pull/|(?i:PR)\s*#)\d+',text): continue
        branch=re.search(r'(?im)^BRANCH:\s*([A-Za-z0-9/_.-]+)',text)
        if branch and any(h['branch']==branch[1] and h['repository'] in repos for h in pr_heads): continue
        date=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()
        relative=str(path.relative_to(root))
        rows.append({'id':relative,'kind':'builder_brief_file','title':path.stem,'state':'pr_unlinked',
            'opened_at':date,'last_activity_at':date,'owner':None,'version':None,'identity':{},
            'completed':False,'cancelled':False,'link':'/control-room/progress',
            'suggested_triage':{'action':'progress','label':'Reassess unlinked builder brief'}})
    return rows


def cached_github(path, report_path=None, now=None, read=github_json, *, branches=local_branches):
    now = now or datetime.now(timezone.utc)
    path = Path(path)
    cache = None
    try:
        cache=json.loads(path.read_text())
        if not valid_cache(cache): raise ValueError('invalid cache')
        date=date_value(cache['observed_at'])
        if cache.get('schema')=='system-work-external.v1' and cache.get('completed_pr_history') and timedelta(0)<=now-date<timedelta(minutes=15): return cache
    except (OSError, ValueError, KeyError, TypeError, AttributeError): pass
    suggestions={}
    try:
        if report_path and Path(report_path).is_file(): suggestions=dot_suggestions(Path(report_path).read_text())
    except (OSError, UnicodeError): pass
    state_path = path.with_suffix(path.suffix + '.prs')
    try:
        saved = json.loads(state_path.read_text())
        state = saved['repositories'] if cache is not None and saved['observed_at'] == cache['observed_at'] else {}
        if not isinstance(state, dict): state = {}
    except (OSError, ValueError, KeyError, TypeError): state = {}
    cache=collect_github(now,read,suggestions,state=state,
                         git_root=path.parent / 'system-work-git', branches=branches)
    try:
        cache["items"].extend(unfinished_briefs(Path.home() / "carr-system/out/orch", cache["pr_heads"]))
        cache.pop("pr_heads", None)
    except OSError:
        cache["briefs_unavailable"] = True
    path.parent.mkdir(parents=True,exist_ok=True)
    # Refresh outside the lock, then serialize monotonic atomic promotion only.
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = json.loads(path.read_text())
            if valid_cache(current) and date_value(current['observed_at']) > date_value(cache['observed_at']):
                return current
        except (OSError, ValueError, KeyError, TypeError): pass
        atomic_json(path, cache)
        atomic_json(state_path, {'observed_at': cache['observed_at'], 'repositories': state})
    return cache


def atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary): os.unlink(temporary)
