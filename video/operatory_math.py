from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict, assert_never


@dataclass(frozen=True)
class _Footage:
    src: str
    dur: float
    push_in: float
    line: str


@dataclass(frozen=True)
class _Counter:
    target: int
    prefix: str
    suffix: str
    delay: float
    dur: float
    orange: bool | None = None


@dataclass(frozen=True)
class _CounterCard:
    dur: float
    title: str
    counter: _Counter
    sub: str


@dataclass(frozen=True)
class _Row:
    label: str
    value: str
    delay: float
    orange: bool | None = None


@dataclass(frozen=True)
class _Net:
    label: str
    target: int
    prefix: str
    suffix: str
    delay: float


@dataclass(frozen=True)
class _StackCard:
    dur: float
    title: str
    rows: tuple[_Row, ...]
    net: _Net


_Shot = _Footage | _CounterCard | _StackCard


@dataclass(frozen=True)
class _EndCard:
    dur: float
    name: str
    title: str
    tagline: str


@dataclass(frozen=True)
class AudioSchedule:
    total: float
    endcard_at: float
    sweeps: tuple[float, ...]
    clicks: tuple[float, ...]


@dataclass(frozen=True)
class OperatoryPlan:
    job_json: str
    audio: AudioSchedule


class _FootagePayload(TypedDict):
    src: str
    dur: float
    pushIn: float
    line: str


class _CounterPayload(TypedDict):
    target: int
    prefix: str
    suffix: str
    delay: float
    dur: float
    orange: NotRequired[bool]


class _CounterCardPayload(TypedDict):
    type: Literal['stat']
    kind: Literal['counter']
    dur: float
    title: str
    counter: _CounterPayload
    sub: str


class _RowPayload(TypedDict):
    label: str
    value: str
    delay: float
    orange: NotRequired[bool]


class _NetPayload(TypedDict):
    label: str
    target: int
    prefix: str
    suffix: str
    delay: float


class _StackCardPayload(TypedDict):
    type: Literal['stat']
    kind: Literal['stack']
    dur: float
    title: str
    rows: list[_RowPayload]
    net: _NetPayload


_ShotPayload = _FootagePayload | _CounterCardPayload | _StackCardPayload


class _EndCardPayload(TypedDict):
    dur: float
    name: str
    title: str
    tagline: str


class _JobPayload(TypedDict):
    compName: str
    width: int
    height: int
    fps: float
    shots: list[_ShotPayload]
    endCard: _EndCardPayload
    outPath: str


def _shot_payload(shot: _Shot) -> _ShotPayload:
    if isinstance(shot, _Footage):
        return {'src': shot.src, 'dur': shot.dur, 'pushIn': shot.push_in, 'line': shot.line}
    if isinstance(shot, _CounterCard):
        counter: _CounterPayload = {
            'target': shot.counter.target, 'prefix': shot.counter.prefix,
            'suffix': shot.counter.suffix, 'delay': shot.counter.delay, 'dur': shot.counter.dur,
        }
        if shot.counter.orange is not None:
            counter['orange'] = shot.counter.orange
        return {'type': 'stat', 'kind': 'counter', 'dur': shot.dur,
                'title': shot.title, 'counter': counter, 'sub': shot.sub}
    if isinstance(shot, _StackCard):
        rows: list[_RowPayload] = []
        for row in shot.rows:
            payload: _RowPayload = {'label': row.label, 'value': row.value, 'delay': row.delay}
            if row.orange is not None:
                payload['orange'] = row.orange
            rows.append(payload)
        net: _NetPayload = {'label': shot.net.label, 'target': shot.net.target,
                            'prefix': shot.net.prefix, 'suffix': shot.net.suffix, 'delay': shot.net.delay}
        return {'type': 'stat', 'kind': 'stack', 'dur': shot.dur,
                'title': shot.title, 'rows': rows, 'net': net}
    assert_never(shot)


def make_operatory_plan(*, operatory: str, reception: str, raw_output: str) -> OperatoryPlan:
    shots: tuple[_Shot, ...] = (
        _Footage(operatory, 5.0, 1.06, 'Out of operatories? Run this math.'),
        _CounterCard(5.0, 'One general operatory can produce',
                     _Counter(350, '$', 'K/yr', 0.7, 1.6),
                     'industry benchmarks run $300K to $600K'),
        _CounterCard(4.5, 'Add two operatories',
                     _Counter(700, '+$', 'K/yr', 0.6, 1.4, True),
                     'in added yearly production capacity'),
        _Footage(reception, 5.0, 1.07, 'Relocation sounds expensive. Price the offsets first.'),
        _StackCard(9.0, 'The real cost, after concessions', (
            _Row('Buildout for the new space', '$500K', 0.7),
            _Row('Landlord TI allowance', '-$180K', 2.4, True),
            _Row('Free rent during buildout', '-$60K', 4.0, True),
        ), _Net('Net cost', 260, '$', 'K', 5.9)),
        _CounterCard(6.0, 'At a 35 percent margin, two ops add',
                     _Counter(245, '$', 'K/yr', 0.7, 1.6),
                     'net cost paid back in about 13 months'),
        _Footage(operatory, 5.0, 1.08, 'The right space can pay for itself. Run your numbers first.'),
    )
    end_card = _EndCard(4.5, 'Joe Bookout', 'HEALTHCARE REAL ESTATE | CARR',
                        'Tenant and buyer representation only')
    sweeps: list[float] = []
    clicks: list[float] = []
    t = 0.0
    for shot in shots:
        if t > 0:
            sweeps.append(t - 0.12)
        if isinstance(shot, _CounterCard):
            clicks.append(t + shot.counter.delay)
        elif isinstance(shot, _StackCard):
            clicks.extend(t + row.delay for row in shot.rows)
            clicks.append(t + shot.net.delay)
        elif not isinstance(shot, _Footage):
            assert_never(shot)
        t += shot.dur
    end_card_payload: _EndCardPayload = {
        'dur': end_card.dur, 'name': end_card.name,
        'title': end_card.title, 'tagline': end_card.tagline,
    }
    job: _JobPayload = {
        'compName': 'CARR_OperatoryMath', 'width': 1080, 'height': 1080, 'fps': 29.97,
        'shots': [_shot_payload(shot) for shot in shots],
        'endCard': end_card_payload, 'outPath': raw_output,
    }
    return OperatoryPlan(json.dumps(job, indent=2),
                         AudioSchedule(t + end_card.dur, t, tuple(sweeps), tuple(clicks)))
