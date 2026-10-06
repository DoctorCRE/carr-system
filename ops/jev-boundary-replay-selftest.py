#!/usr/bin/env python3
"""Offline acceptance for the labeled Jev boundary replay."""
import importlib.util
import json
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("jev_boundary_replay", repo / "ops/jev-boundary-replay.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fixture = json.loads(module.FIXTURE.read_text())
have_logs = all((repo / path).exists() for path in fixture["source_logs"])
rows = module.labeled_replay(verify_sources=have_logs)
by_id = {row["id"]: row for row in rows}
assert len(rows) >= 5
for row in rows:
    assert row["after"]["detected"] >= row["before"]["detected"], row
    assert row["after"]["false_positives"] <= row["before"]["false_positives"], row
# Triage is deterministic here and #1528 retired the injection screen: no paid call.
assert by_id["failed_test_injection"]["after"]["calls"] == 0
assert set(by_id["failed_test_injection"]["after"]["found"]) == {"failure", "ci_failed"}
for case_id in ("unsupported_stop", "ordinary_diff_stop"):
    assert "done_review" in by_id[case_id]["after"]["found"], by_id[case_id]
    assert "done_unsupported" not in by_id[case_id]["after"]["found"], by_id[case_id]
assert by_id["ordinary_diff_stop"]["before"]["calls"] == 0  # legacy dispatch uses current predicates
assert by_id["ordinary_diff_stop"]["after"]["calls"] == 0
assert by_id["repeated_stop"]["after"]["calls"] == 0
if have_logs:
    spend = module.spend_replay()
    assert set(spend) == {"2026-09-26", "2026-09-27"}
    assert all(day["before_input_tokens"] > day["after_input_tokens_conservative"]
               for day in spend.values())
else:
    assert all(len(case["source_row_sha256"]) == 64 for case in fixture["cases"])
print("jev-boundary-replay-selftest: labeled cases pass; recorded log " +
      ("anchors and spend verified" if have_logs else "anchors unavailable on runner"))
