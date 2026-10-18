"""Cached GitHub census for the existing progress-board publisher, never a page read.

Read-only GitHub operations. The cache is projection data in board_snapshot;
source identity and suggestions do not grant branch or pull-request authority.
"""
import json
import fcntl
import os
import tempfile
import re
import subprocess
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from pathlib import Path

REPOSITORIES = ("jbookout/carr-system", "jbookout/doctorcre-app")


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


def pages_of_rows(value):
    if not isinstance(value, list) or not value or any(not isinstance(page, list) for page in value):
        raise ValueError('GitHub pagination envelope invalid')
    rows = [row for page in value for row in page]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('GitHub row invalid')
    return rows


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


def collect_github(now, read=github_json, suggestions=None):
    items, coverage, pr_heads, pr_work_refs = [], [], [], set()
    for repo in REPOSITORIES:
        try:
            pr_pages = read(['api',f'repos/{repo}/pulls?state=all&per_page=100','--paginate','--slurp'])
            prs = pages_of_rows(pr_pages)
            for pr in prs:
                if type(pr['number']) is not int or pr['number'] < 1:
                    raise ValueError('PR number invalid')
                required_text(pr['title']); required_text(pr['html_url']); required_text(pr['head']['ref'])
                date_value(pr['created_at']); date_value(pr['updated_at'])
                if pr.get('merged_at') is not None: date_value(pr['merged_at'])
                if pr.get('state', 'open') not in ('open', 'closed'): raise ValueError('PR state invalid')
                pr_heads.append({'repository':repo,'branch':pr['head']['ref']})
                pr_work_refs.update(re.findall(r'WR-\d+',pr.get('body') or ''))
                completed = bool(pr.get('merged_at'))
                if pr.get('state','open') != 'open' and not completed: continue
                items.append({'id': f"{repo}:pr:{pr['number']}", 'kind':'pull_request','title':pr['title'],
                    'state':'merged' if completed else 'open', 'opened_at':pr['created_at'],
                    'last_activity_at':pr.get('merged_at') or pr['updated_at'], 'owner':None,'version':None,
                    'completed':completed,'cancelled':False,'link':pr['html_url'],
                    'branch':pr['head']['ref'],'work_refs':sorted(set(re.findall(r'WR-\d+',pr.get('body') or ''))),'identity':{}})
            pages = read(['api',f'repos/{repo}/branches?per_page=100','--paginate','--slurp'])
            for branch in pages_of_rows(pages):
                name=required_text(branch['name'])
                if name=='main': continue
                commit = read(['api',f"repos/{repo}/commits/{branch['commit']['sha']}"])
                date=commit['commit']['committer']['date']
                date_value(date)
                if now-datetime.fromisoformat(date.replace('Z','+00:00')) <= timedelta(days=7): continue
                compare=read(['api',f'repos/{repo}/compare/main...{quote(name,safe="")}'])
                if not isinstance(compare,dict) or type(compare.get('ahead_by')) is not int or compare['ahead_by'] < 0: raise ValueError('compare invalid')
                if compare['ahead_by']==0: continue
                items.append({'id':f'{repo}:branch:{name}','kind':'remote_branch','title':name,'state':'unmerged',
                    'opened_at':date,'last_activity_at':date,'owner':None,'version':branch['commit']['sha'],
                    'completed':False,'cancelled':False,'link':f'https://github.com/{repo}/tree/{quote(name,safe="")}',
                    'identity':{},'suggested_triage':(suggestions or {}).get(name) if repo.endswith('/carr-system') else None})
            coverage.append({'repository':repo,'complete':True,'reason':None})
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


def cached_github(path, report_path=None, now=None, read=github_json):
    now = now or datetime.now(timezone.utc)
    path = Path(path)
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
    cache=collect_github(now,read,suggestions)
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
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump(cache, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary): os.unlink(temporary)
    return cache
