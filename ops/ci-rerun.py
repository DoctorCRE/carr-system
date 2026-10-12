#!/usr/bin/env python3
"""Rerun one failed/cancelled CI class job, at most twice per run.

Usage: ops/ci-rerun.sh RUN_ID --job JOB_ID [--repo OWNER/REPO].
The default repository is jbookout/carr-system. Successful jobs are retained;
GitHub also reruns dependents. Local checks precede policy admission and the
remote attempt/head is rechecked immediately before the single dispatch.
Other repositories use their associated PR head; carr-system also requires
that head to match this checkout.
"""
from __future__ import annotations
import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.platform_metering import authorize_metered_execution, MeteringRefusal

spec = importlib.util.spec_from_file_location('ci_evidence', ROOT / 'ops/ci-evidence.py')
assert spec is not None and spec.loader is not None
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)
REPO = 'jbookout/carr-system'
ALLOWED_REPOS = (REPO, 'jbookout/doctorcre-app', 'jbookout/software-factory')
MAX_ATTEMPTS = 3  # Initial run plus two job-only retries; job timeouts unchanged.


class Refusal(RuntimeError):
    pass


def admit(run, job, pr, head):
    if (run.get('name') != 'CI' or run.get('path') != '.github/workflows/ci.yml'
            or run.get('event') != 'pull_request' or run.get('status') != 'completed'
            or run.get('conclusion') not in ('failure', 'cancelled')
            or run.get('head_sha') != head or pr.get('state') != 'open'
            or pr.get('head', {}).get('sha') != head):
        raise Refusal('requires completed failed/cancelled CI at the open PR and candidate head')
    attempt = run.get('run_attempt')
    if type(attempt) is not int or not 1 <= attempt < MAX_ATTEMPTS:
        raise Refusal('CI retry budget exhausted (initial run plus two retries)')
    if (job.get('run_id') != run.get('id') or job.get('status') != 'completed'
            or job.get('conclusion') not in ('failure', 'cancelled')):
        raise Refusal('job must belong to this run and be failed/cancelled')
    groups = {'ops/ci.sh --strict --only ' + g: g for g in evidence.class_groups()}
    if job.get('name') not in groups:
        raise Refusal('only a named CI class job may be rerun')
    return groups[job['name']]


def rerun(remote, run_id, job_id, head, policy, checks, dispatch):
    """Pin the candidate to the supplied local head, or the fetched PR head."""
    snapshot = remote.snapshot(run_id, job_id)
    if head is None:
        head = snapshot[2].get('head', {}).get('sha')
        if not head:
            raise Refusal('PR head unavailable; no dispatch')
    group = admit(*snapshot, head)
    checks(group)
    authorize_metered_execution(policy, 'github-actions-remote-ci',
                               {'candidate_sha': head, 'local_checks_green': True})
    current = remote.snapshot(run_id, job_id)
    admit(*current, head)
    if current != snapshot:
        raise Refusal('run, job or PR changed during local checks; no dispatch')
    dispatch(run_id, job_id, current[0]['run_attempt'], head)


class GitHub:
    def __init__(self, repo=REPO):
        if repo not in ALLOWED_REPOS:
            raise Refusal('repository is not allowlisted')
        self.repo = repo

    def api(self, path):
        out = subprocess.run(['gh', 'api', f'repos/{self.repo}/{path}'],
                             capture_output=True, text=True, timeout=60, check=True)
        return json.loads(out.stdout)

    def snapshot(self, run_id, job_id):
        run = self.api(f'actions/runs/{run_id}')
        job = self.api(f'actions/jobs/{job_id}')
        prs = run.get('pull_requests', [])
        if len(prs) != 1:
            raise Refusal('requires exactly one associated PR')
        pr = self.api(f'pulls/{prs[0]["number"]}')
        jobs = self.api(f'actions/runs/{run_id}/jobs?filter=latest&per_page=100')
        if job_id not in [j['id'] for j in jobs['jobs']]:
            raise Refusal('job is not a current run result')
        return run, job, pr


def local_checks(group):
    command = ([str(ROOT / 'run.sh'), 'local-db-ci', '--class', 'migration']
               if group == 'migration' else [str(ROOT / 'ops/ci.sh'), '--strict', '--only', group])
    budget = (35 if group == 'migration' else 20) * 60
    result = subprocess.run([sys.executable, str(ROOT / 'bin/with-timeout.py'), str(budget), *command], cwd=ROOT)
    if result.returncode:
        raise Refusal('local CI failed; no remote retry consumed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id', type=int)
    parser.add_argument('--job', required=True, type=int)
    parser.add_argument('--repo', choices=ALLOWED_REPOS, default=REPO)
    args = parser.parse_args()
    if args.run_id <= 0 or args.job <= 0:
        parser.error('run and job IDs must be positive')
    try:
        if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
            raise Refusal('commit tracked changes before rerunning CI')
        local_head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        policy = json.loads((ROOT / 'ops/config/platform-metering.v1.json').read_text())
        lock_dir = Path(tempfile.gettempdir()) / 'carr-ci-rerun'
        # Keep existing default-repository reservations effective after upgrade.
        if args.repo != REPO:
            lock_dir /= args.repo.replace('/', '--')
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (lock_dir / f'{args.run_id}.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            remote = GitHub(args.repo)
            def dispatch(run_id, job_id, attempt, head):
                reservation = lock_dir / f'{run_id}-{attempt}.json'
                if reservation.exists():
                    raise Refusal('attempt already dispatched or uncertain; inspect hosted run')
                if subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip() != local_head:
                    raise Refusal('local head changed during checks')
                if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
                    raise Refusal('local tracked changes appeared during checks')
                # Reserve before dispatch. An ambiguous timeout must not silently
                # spend another retry. Hosted run_attempt is the durable limit.
                with reservation.open('x') as receipt:
                    json.dump({'repo':args.repo,'run_id':run_id,'job_id':job_id,'attempt':attempt,'head_sha':head},receipt)
                result = subprocess.run(['gh','run','rerun',str(run_id),'--job',str(job_id),'--repo',args.repo],
                                        capture_output=True, timeout=60)
                if result.returncode:
                    raise Refusal('dispatch refused or uncertain; reservation retained')
                print(f'CI job retry submitted: run {run_id}, job {job_id}, next attempt {attempt + 1}/{MAX_ATTEMPTS}')
            head = local_head if args.repo == REPO else None
            rerun(remote,args.run_id,args.job,head,policy,local_checks,dispatch)
        return 0
    except (Refusal, MeteringRefusal) as exc:
        print(f'CI rerun refused: {exc}', file=sys.stderr)
        return 1
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        print('CI rerun refused: GitHub/local validation unavailable; no automatic retry',file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
