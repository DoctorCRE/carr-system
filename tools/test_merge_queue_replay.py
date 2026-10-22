"""Worked schedules for the file-history replay (no provider or wall-clock I/O)."""
import unittest
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.merge_queue.replay import replay


class ReplayTests(unittest.TestCase):
    def test_overlap_recomposes_but_disjoint_green_head_lands(self):
        # A and B share a path; C can land with A. B needs a second CI run.
        prs = [dict(number=n,files=files) for n,files in
               [(1,['a']), (2,['a']), (3,['c'])]]
        result = replay(prs, size=3, ci_seconds=30, canary_seconds=25, landing_seconds=2)
        self.assertEqual(result['ci_waves'],2)
        self.assertEqual(result['ci_runs'],4)
        self.assertEqual(result['recompositions'],1)
        self.assertEqual(result['landing_order'],[1,3,2])
        self.assertEqual(result['last_landing_seconds'],66)
        self.assertEqual(result['wall_seconds'],91)

    def test_one_red_canary_retry_adds_time_without_extra_landing_or_ci(self):
        prs = [dict(number=1,files=['a'])]
        clean = replay(prs,size=1,ci_seconds=30,canary_seconds=25,landing_seconds=2)
        flaky = replay(prs,size=1,ci_seconds=30,canary_seconds=25,landing_seconds=2,flakes={1})
        self.assertEqual(flaky['wall_seconds'],82)
        self.assertEqual(flaky['wall_seconds']-clean['wall_seconds'],25)
        self.assertEqual(flaky['canary_attempts'],2)
        self.assertEqual(flaky['ci_runs'],1)
        self.assertEqual(flaky['landing_order'],[1])

    def test_finite_canary_capacity_is_included_in_wall_time(self):
        prs = [dict(number=n,files=[str(n)]) for n in range(1,5)]
        result = replay(prs,size=4,ci_seconds=30,canary_seconds=25,landing_seconds=2,canary_workers=1)
        self.assertEqual(result['last_landing_seconds'],38)
        self.assertEqual(result['wall_seconds'],132)

    def test_replay_reuses_generated_artifact_collision_rules(self):
        prs = [dict(number=1,files=['ops/config/rule-triage.v1.json']),
               dict(number=2,files=['mcp-server/src/core-rule-ids.js'])]
        result = replay(prs,size=4)
        self.assertEqual(result['ci_waves'],2)
        self.assertEqual(result['recompositions'],1)

    def test_saved_fifty_pr_population_lands_every_pr_exactly_once(self):
        source = Path(__file__).parent / 'fixtures/merge_queue/merged-prs-2026-10-22.json'
        prs = json.loads(source.read_text())['prs']
        self.assertEqual(len(prs),50)
        expected = sorted(pr['number'] for pr in prs)
        self.assertEqual(len(set(expected)),50)
        for size in (1,4,6):
            result = replay(prs,size=size,flakes={pr['number'] for pr in prs[9::10]})
            self.assertEqual(sorted(result['landing_order']),expected)
            self.assertEqual(result['canary_attempts'],55)


if __name__ == '__main__':
    unittest.main()
