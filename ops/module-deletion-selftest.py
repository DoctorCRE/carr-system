#!/usr/bin/env python3
"""Check that closed module deletions leave truthful retained contracts."""
import json
from pathlib import Path
import subprocess
import unittest


REPO = Path(__file__).resolve().parents[1]


class ModuleDeletionTests(unittest.TestCase):
    def test_exclusive_data_leaves_with_its_deleted_readers(self):
        for path in (
            "mcp-server/test/fixtures/siep-16-scac-pop.v1.json",
            "evals/retrieval/fixtures/bounded-record-questions.v1.json",
            "evals/retrieval/baselines/bounded-record-strict-fts.v1.json",
        ):
            with self.subTest(path=path):
                self.assertFalse((REPO / path).exists(), path)

    def test_retained_contracts_do_not_claim_deleted_implementations(self):
        result = subprocess.run([
            "node", "--input-type=module", "-e",
            "import {v5ModelRoutingProjection} from './mcp-server/src/model-routing.v5.js';"
            "import {v5CostVarianceProjection} from './mcp-server/src/cost-variance-replan.v5.js';"
            "console.log(JSON.stringify([v5ModelRoutingProjection(), v5CostVarianceProjection()]));",
        ], cwd=REPO, check=True, capture_output=True, text=True)
        contracts = json.loads(result.stdout)
        for contract in contracts:
            for gap in contract["unimplemented_dependencies"]:
                self.assertNotRegex(gap, r"model-qualification-kernel|expected-total-cost|adapter boundary")
        for path in ("rule-applicability", "partner-mail-calendar", "cost-variance-replan"):
            with self.subTest(path=path):
                self.assertNotRegex(
                    (REPO / f"mcp-server/src/{path}.v5.js").read_text(),
                    r"context-assembly|expected-total-cost",
                )
        guards = (REPO / "mcp-server/src/rule-applicability.v5.js").read_text()
        self.assertIn("rule-context-runtime.v5.js", guards)

    def test_active_consumers_and_routes_do_not_name_retired_parity_commands(self):
        for path in (
            "lib/record_sources.py", "generators/build-deal-room.py",
            "generators/build-lead-board.py", "shared/build-lead-board-template.py",
            "hooks/draft-export-gate.py", "ops/draft-export-gate-selftest.py",
            "specs/party-graph-ref-fallback.md", "ops/config/rule-routes.v1.json",
        ):
            with self.subTest(path=path):
                self.assertNotRegex((REPO / path).read_text(), r"parity-lead-board|parity-records")

    def test_health_scan_explains_only_its_retained_self_skip(self):
        source = (REPO / "tools/health-check.py").read_text()
        start = source.index("# A WATCHER NAMING A FILE IS NOT A CONSUMER OF IT.")
        end = source.index('if os.path.basename(_f) == "health-check.py":', start)
        explanation = source[start:end]
        self.assertIn("WATCH list", explanation)
        self.assertIn("health-check.py", explanation)
        self.assertNotRegex(explanation, r"parity harness|Five of the six|six warnings")


if __name__ == "__main__":
    unittest.main()
