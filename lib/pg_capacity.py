"""Read-only SysV capacity for the provisional eight-job Studio envelope."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

JOBS = 8
CLUSTERS_PER_JOB = 9
BACKGROUND = 16
RESERVE = 8
PROFILE_SHMMNI = 256
PROFILE_SHMALL = 65536


@dataclass(frozen=True)
class Segment:
    identifier: int
    size: int
    creator: int


@dataclass(frozen=True)
class CapacitySnapshot:
    shmmni: int
    shmall: int
    shmmax: int
    shmseg: int
    page_bytes: int
    hardware_page_bytes: int
    segments: tuple[Segment, ...]


def parse_darwin(limits, table):
    values = {}
    for line in limits.splitlines():
        key, value = line.split(':', 1)
        values[key.strip()] = int(value)
    keys = ('kern.sysv.shmmni', 'kern.sysv.shmall', 'kern.sysv.shmmax',
            'kern.sysv.shmseg', 'hw.pagesize')
    if any(key not in values or values[key] <= 0 for key in keys):
        raise ValueError('missing or invalid SysV sysctl values')
    header = None
    segments = []
    if 'Shared Memory:' not in table:
        raise ValueError('missing ipcs shared memory table')
    for line in table.splitlines():
        fields = line.split()
        if fields[:2] == ['T', 'ID']:
            header = fields
        elif fields and fields[0] == 'm':
            if not header or len(fields) != len(header):
                raise ValueError('invalid ipcs shared memory row')
            row = dict(zip(header, fields))
            segments.append(Segment(int(row['ID']), int(row['SEGSZ']), int(row['CPID'])))
    if header is None or not {'ID', 'SEGSZ', 'CPID'} <= set(header):
        raise ValueError('missing ipcs size/creator columns')
    if any(s.identifier < 0 or s.size <= 0 or s.creator <= 0 for s in segments):
        raise ValueError('invalid ipcs segment values')
    if len({s.identifier for s in segments}) != len(segments):
        raise ValueError('duplicate ipcs segment identifier')
    # macOS SHMALL counts 4 KiB pages, independently of hardware page size.
    # https://www.postgresql.org/docs/18/kernel-resources.html
    return CapacitySnapshot(*(values[key] for key in keys[:4]), 4096,
                            values['hw.pagesize'], tuple(segments))


def read_capacity():
    def read(command):
        return subprocess.run(command, check=True, capture_output=True, text=True,
                              timeout=5).stdout
    if sys.platform == 'darwin':
        return parse_darwin(read(['sysctl', 'kern.sysv.shmmni', 'kern.sysv.shmall',
                                  'kern.sysv.shmmax', 'kern.sysv.shmseg', 'hw.pagesize']),
                            read(['ipcs', '-m', '-a']))
    if sys.platform == 'linux':
        values = [int(Path('/proc/sys/kernel', key).read_text()) for key in ('shmmni', 'shmall', 'shmmax')]
        lines = Path('/proc/sysvipc/shm').read_text().splitlines()
        if not lines or not {'shmid', 'size', 'cpid'} <= set(lines[0].split()):
            raise ValueError('missing Linux SysV size/creator columns')
        segments = []
        for line in lines[1:]:
            row = dict(zip(lines[0].split(), line.split(), strict=True))
            segments.append(Segment(int(row['shmid']), int(row['size']), int(row['cpid'])))
        page = os.sysconf('SC_PAGE_SIZE')
        return CapacitySnapshot(*values, values[0], page, page, tuple(segments))
    raise ValueError(f'unsupported SysV capacity platform {sys.platform}')


def evaluate(snapshot):
    allocation_page = max(snapshot.page_bytes, snapshot.hardware_page_bytes)
    pages_per_allocation = allocation_page // snapshot.page_bytes
    used_pages = sum(((s.size + allocation_page - 1) // allocation_page)
                     * pages_per_allocation for s in snapshot.segments)
    additional = JOBS * CLUSTERS_PER_JOB + RESERVE
    required_slots = max(PROFILE_SHMMNI, BACKGROUND + additional,
                         len(snapshot.segments) + additional)
    required_pages = max(PROFILE_SHMALL, used_pages + additional * pages_per_allocation)
    sufficient = (snapshot.shmmni >= required_slots and snapshot.shmall >= required_pages
                  and snapshot.shmmax >= 56 and snapshot.shmseg >= 1)
    return {
        'outcome': 'sufficient' if sufficient else 'insufficient',
        'demand': {'jobs': JOBS, 'clusters_per_job': CLUSTERS_PER_JOB,
                   'background_floor': BACKGROUND, 'reserve': RESERVE},
        'observed': {**asdict(snapshot), 'segments': [asdict(s) for s in snapshot.segments],
                     'used_slots': len(snapshot.segments),
                     'used_pages': used_pages},
        'required': {'shmmni': required_slots, 'shmall': required_pages, 'shmmax': 56, 'shmseg': 1},
        'action': ('continue verification' if sufficient else
                   'provision Studio boot profile 256/65536, reboot once, and rerun readback; '
                   'if occupancy exceeds the envelope, coordinate workloads; never reclaim foreign IPC'),
        'scope': 'point-in-time envelope check, not atomic admission or eight-job acceptance',
    }


def preflight():
    try:
        return evaluate(read_capacity())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return {'outcome': 'unreadable', 'error': str(error),
                'action': 'restore readable sysctl/ipcs capacity evidence before PostgreSQL verification'}


def main():
    result = preflight()
    print(json.dumps(result, sort_keys=True))
    return {'sufficient': 0, 'insufficient': 1, 'unreadable': 78}[result['outcome']]


if __name__ == '__main__':
    sys.exit(main())
