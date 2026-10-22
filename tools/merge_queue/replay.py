"""Deterministic queue/file-history model; timings are assumptions, not a forecast.

Each wave fills the top N window, waits for CI, then lands reusable green heads
in queue order. Invalidated heads need the next wave. Canary workers run beside
CI; a configured red-then-green flake consumes one full extra canary duration.
Local composition time and provider outages are outside this model. The real-Git
six-PR daemon tests independently verify the scheduler's disjoint-file ceiling.
"""
from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path

from tools.merge_queue.overlap import overlaps


def replay(prs, *, size=4, ci_seconds=1800, canary_seconds=1500,
           landing_seconds=12, canary_workers=4, flakes=()):
    pending = list(prs)
    landed, order, canaries = [], [], [0]*canary_workers
    now = waves = runs = recompositions = attempts = 0
    if size < 1 or canary_workers < 1 or min(ci_seconds,canary_seconds,landing_seconds) < 0:
        raise ValueError('Positive capacity and nonnegative durations required')
    while pending:
        wave = pending[:size]
        base = len(landed)
        waves += 1
        runs += len(wave)
        now += ci_seconds
        for pr in wave:
            if any(overlaps(pr['files'], paths) for paths in landed[base:]):
                recompositions += 1
                continue
            now += landing_seconds
            landed.append(pr['files'])
            order.append(pr['number'])
            pending.remove(pr)
            # FIFO finite canary capacity. Canary flakes do not restart head CI.
            start = max(now, heapq.heappop(canaries))
            count = 2 if pr['number'] in flakes else 1
            attempts += count
            heapq.heappush(canaries, start+count*canary_seconds)
    return dict(size=size, merged=len(order), ci_waves=waves, ci_runs=runs,
                recompositions=recompositions, canary_attempts=attempts,
                last_landing_seconds=now, wall_seconds=max([now]+canaries),
                landing_order=order)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    prs = snapshot['prs']
    flakes = {pr['number'] for pr in prs[9::10]}
    print(json.dumps(dict(source=snapshot['source'], assumptions=dict(
        ci_seconds=1800,canary_seconds=1500,canary_workers=4,
        landing_seconds=12,canary_red_then_green=sorted(flakes),
        excluded=['local composition','provider outages','persistent canary failures']),
        results=[replay(prs,size=n,flakes=flakes) for n in (1,4,6)]),indent=2))


if __name__ == '__main__':
    main()
