#!/usr/bin/env python3
"""Mutation tests for the Worker billing configuration gate."""
import copy
import importlib.util
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("matrix", ROOT / "ops/environment-matrix-selftest.py")
assert SPEC and SPEC.loader
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


class BillingGateTests(unittest.TestCase):
    def setUp(self):
        self.config = tomllib.loads((ROOT / "mcp-server/wrangler.toml").read_text())

    def test_shipped_configuration_is_bounded(self):
        self.assertEqual(matrix.billing_failures(self.config), [])

    def test_every_environment_needs_cpu_and_sampling_limits(self):
        for name in ("production", "staging", "preview"):
            for field, value in (("cpu_ms", None), ("cpu_ms", 30000),
                                 ("head_sampling_rate", None), ("head_sampling_rate", 1)):
                with self.subTest(name=name, field=field, value=value):
                    config = copy.deepcopy(self.config)
                    target = config if name == "production" else config.setdefault("env", {}).setdefault(
                        name, copy.deepcopy(config["env"]["staging"]))
                    table = "limits" if field == "cpu_ms" else "observability"
                    target.setdefault(table, {})[field] = value
                    self.assertTrue(matrix.billing_failures(config))

    def test_production_and_staging_keep_the_required_sampling_rates(self):
        self.config["observability"]["head_sampling_rate"] = 0.2
        self.assertTrue(matrix.billing_failures(self.config))

    def test_named_production_cannot_hide_the_default_worker(self):
        self.config["env"]["production"] = copy.deepcopy(self.config["env"]["staging"])
        self.config["env"]["production"]["observability"]["head_sampling_rate"] = 0.1
        self.config["observability"]["head_sampling_rate"] = 1
        self.assertTrue(matrix.billing_failures(self.config))

    def test_logs_override_cannot_disable_sampling(self):
        for target in (self.config, self.config["env"]["staging"]):
            with self.subTest(target=target["vars"]["CARR_ENV"]):
                target["observability"]["logs"] = {"enabled": True, "head_sampling_rate": 1}
                self.assertTrue(matrix.billing_failures(self.config))
                del target["observability"]["logs"]


if __name__ == "__main__":
    unittest.main()
