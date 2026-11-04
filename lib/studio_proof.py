"""Bounded semantic proof under the trusted scheduler/producer/recorder premise.

Canonical acquisition is internal. Files supplied by callers are never acquired runs.
Machine attribution is a separate collector fact, absent from ordinary ops.run.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import re
from uuid import UUID

SHA = re.compile(r'[0-9a-f]{40}')
KEY = re.compile(r'[a-z][a-z0-9_]{0,63}')


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('aware execution time required')
    return value


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    service: str
    run_key: str
    environment: str
    target: str
    count: str
    minimum: int
    max_age_seconds: int

    def __post_init__(self):
        if (self.target != 'studio' or self.environment not in {'production', 'local', 'staging', 'rehearsal'}
                or not self.service or not self.run_key or not self.requirement_id
                or not all(KEY.fullmatch(k) for k in self.count.split('.'))
                or type(self.minimum) is not int or self.minimum < 0
                or type(self.max_age_seconds) is not int or not 1 <= self.max_age_seconds <= 604800):
            raise ValueError('invalid bounded Studio requirement')


@dataclass(frozen=True)
class Source:
    repository: str
    merge_sha: str
    release_sha: str
    target: str


@dataclass(frozen=True)
class Installation:
    target: str
    runtime_sha: str
    started_at: datetime | None
    ended_at: datetime | None
    helper_verified: bool
    plists_verified: bool
    registered_command_verified: bool
    clean_executables: bool


@dataclass(frozen=True)
class Run:
    run_id: str
    correlation_id: str
    service: str
    run_key: str
    environment: str
    source_kind: str
    source_ref: str
    state: str
    exit_code: int | None
    started_at: datetime | None
    ended_at: datetime | None
    observed_at: datetime | None
    expires_at: datetime | None
    runtime_sha: str | None
    reason: str | None
    counts: dict
    receipt_state: str | None
    problem: str | None

    @classmethod
    def from_record(cls, record):
        value = dict(record)
        problem = None
        try:
            for key in ('run_id', 'correlation_id'):
                value[key] = str(UUID(str(value[key])))
            for key in ('started_at', 'ended_at', 'observed_at', 'expires_at'):
                value[key] = timestamp(value[key]) if value[key] is not None else None
            if value['started_at'] is None or value['ended_at'] is None or value['ended_at'] < value['started_at']:
                raise ValueError('execution interval unavailable')
        except (ValueError, TypeError, KeyError):
            problem = 'canonical identity or execution interval unavailable'
        receipt = {}
        try:
            detail = value.pop('detail')
            if not isinstance(detail, str) or len(detail.encode()) > 16384:
                raise ValueError('detail size')
            receipt = json.loads(detail)
            if not isinstance(receipt, dict):
                raise ValueError('receipt object')
            counts = receipt['counts']
            groups = list(counts.values()) if set(counts) == {'run', 'backlog'} else [counts]
            if not all(isinstance(g, dict) and all(isinstance(k, str) and KEY.fullmatch(k)
                    and type(v) is int and 0 <= v <= 1_000_000_000 for k, v in g.items()) for g in groups):
                raise ValueError('counts')
            if receipt.get('state') not in {'succeeded', 'failed', 'skipped', 'degraded'}:
                raise ValueError('receipt state')
            if not isinstance(receipt['reason'], str) or not KEY.fullmatch(receipt['reason']):
                raise ValueError('reason')
        except (KeyError, TypeError, ValueError):
            problem = problem or 'sanitized semantic receipt unavailable'
            receipt = {}
        value.pop('detail', None)
        runtime = receipt.get('runtime_sha')
        if not isinstance(runtime, str) or not SHA.fullmatch(runtime):
            receipt['runtime_sha'] = None
        return cls(**value, runtime_sha=receipt.get('runtime_sha'), reason=receipt.get('reason'),
                   counts=receipt.get('counts', {}), receipt_state=receipt.get('state'), problem=problem)


@dataclass(frozen=True)
class RunBatch:
    status: str
    complete: bool
    truncated: bool
    since: datetime
    until: datetime
    runs: tuple[Run, ...] = ()

    def projection(self):
        return {'schema': 'carr-canonical-runs.v1', **asdict(self)}


@dataclass(frozen=True)
class Evidence:
    batch: RunBatch
    installation: Installation | None
    release_sha: str | None
    target_bindings: dict[str, str]
    missing: tuple[str, ...] = ()
    source_ancestry_verified: bool = False


@dataclass(frozen=True)
class Observation:
    state: str
    requirement_id: str
    reasons: tuple[str, ...]
    source: Source
    run_id: str | None = None
    correlation_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


def observe(requirement, source, evidence, clock):
    """Project one bounded snapshot. Collectors, never JSON flags, own trust facts."""
    def result(state, reason, run=None):
        return Observation(state, requirement.requirement_id, (reason,), source,
                           *([run.run_id, run.correlation_id, run.started_at, run.ended_at] if run else []))
    clock = timestamp(clock)
    batch = evidence.batch
    if batch.status != 'ok' or not batch.complete or batch.truncated:
        return result('held', 'canonical acquisition unavailable or incomplete')
    if evidence.missing:
        return result('held', '; '.join(evidence.missing))
    if (source.repository != 'DoctorCRE/carr-system' or source.target != requirement.target
            or not isinstance(source.merge_sha, str) or not SHA.fullmatch(source.merge_sha)
            or not isinstance(source.release_sha, str) or not SHA.fullmatch(source.release_sha)
            or evidence.release_sha != source.release_sha or evidence.source_ancestry_verified is not True):
        return result('held', 'verified source/release identity unavailable')
    install = evidence.installation
    if install is None:
        return result('held', 'installation interval and verified local configuration unavailable')
    if (install.target != requirement.target or install.runtime_sha != source.release_sha
            or not all(v is True for v in (install.helper_verified, install.plists_verified,
                                           install.registered_command_verified, install.clean_executables))):
        return result('held', 'installed source/helper/plists/registered command/clean executables unverified')
    try:
        install_start, install_end = timestamp(install.started_at), timestamp(install.ended_at)
        if not install_start <= install_end <= clock:
            raise ValueError('interval')
    except (TypeError, ValueError):
        return result('held', 'actual installation interval unavailable')
    floor = max(install_end, clock-timedelta(seconds=requirement.max_age_seconds))
    if batch.since > floor or batch.until < clock:
        return result('held', 'canonical acquisition does not cover required execution interval')
    rows = batch.runs
    if not rows:
        return result('held', 'fresh canonical execution unavailable')
    for run in rows:
        if (run.service != requirement.service or run.run_key != requirement.run_key
                or run.environment != requirement.environment
                or run.source_kind != 'wrapper' or run.source_ref != 'bin/run-scheduled.sh'):
            return result('held', 'canonical service/run/environment/source mismatch')
        try:
            UUID(run.run_id)
            UUID(run.correlation_id)
            start, end = timestamp(run.started_at), timestamp(run.ended_at)
            if end < start or end > clock:
                raise ValueError('interval')
        except (TypeError, ValueError):
            return result('held', 'canonical identity or execution interval unavailable')
        if evidence.target_bindings.get(run.run_id) != requirement.target:
            return result('held', 'canonical run to installed registered command target binding unavailable')
    if len({r.run_id for r in rows}) != len(rows):
        return result('held', 'duplicate canonical run identity')
    newest = max(r.ended_at for r in rows)
    winners = [r for r in rows if r.ended_at == newest]
    if len(winners) != 1:
        return result('held', 'execution ordering ambiguous')
    run = winners[0]
    if run.started_at < floor:
        return result('held', 'execution predates installation or maximum age', run)
    # An unknown-SHA failure must not vanish behind an older matching success.
    if run.state in {'failed', 'timed_out', 'cancelled'}:
        if run.runtime_sha not in (None, source.release_sha):
            return result('held', 'newest failed execution has a different runtime source', run)
        return result('failed', 'newest canonical execution failed; runtime identity may be unavailable', run)
    if run.problem:
        return result('held', run.problem, run)
    if run.reason in {'capability_not_verified', 'snapshot_unavailable', 'executor_unavailable'}:
        return result('held', 'declared capability unavailable', run)
    if run.state != 'succeeded' or run.receipt_state != 'succeeded' or type(run.exit_code) is not int or run.exit_code != 0:
        return result('held', 'newest execution is not successful with a successful semantic receipt', run)
    if run.runtime_sha != source.release_sha:
        return result('held', 'newest execution full runtime source mismatch or unknown', run)
    try:
        count = run.counts
        for key in requirement.count.split('.'):
            count = count[key]
        if type(count) is not int or count < requirement.minimum:
            raise ValueError('predicate')
    except (KeyError, TypeError, ValueError):
        return result('held', 'declared aggregate count predicate unmet', run)
    return result('live-proven', 'declared semantic predicate under trusted execution-owner collection', run)


def read_runs(service, run_key, environment, since, until, limit=1000):
    """Acquire through the existing authenticated reader, never a supplied excerpt."""
    path = Path(__file__).resolve().parents[1] / 'tools/ops-record.py'
    spec = importlib.util.spec_from_file_location('studio_canonical_reader', path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader.read_run_batch(service, run_key, environment, since, until, limit)


def acquire_evidence(requirement, since, until):
    """Current ledger has no host/registered-command association or install interval.

    Do not infer either from settings, service ownership, a hostname or JSON flags.
    A future trusted execution collector must supply these facts at this seam.
    """
    batch = read_runs(requirement.service, requirement.run_key, requirement.environment, since, until)
    return Evidence(batch, None, None, {}, (
        'canonical run to installed registered command target binding unavailable',
        'actual installation interval and release/config/helper/plists/clean executables unverified'))
