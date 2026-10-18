"""One serial acceptance inventory, two audited groups, and shadow report parity.

Schema/bootstrap and canonical CI remain ordered in every owned cluster. The
trial does not partition ops/ci.sh's migration proofs or authorize gate changes.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Program:
    id: str
    path: str
    shard: int
    kind: str = "python"


# Mutation audit: keep F03's committed engineering fixture, rule-delivery's
# autocommit rule/authority edits and the engineering/ownership/assurance chain
# together, in serial order. The other group owns its committed continuity, Leads and
# calendar/nightly rows. Renewal/incident/completion probes roll back their own
# fixtures. Each group starts from the same full migration class and historical
# fingerprint; no group consumes another group's fixture. Snapshot verification
# stays terminal in group 1 and never writes the tracked snapshot.
PROGRAMS = (
    Program("f03", "tools/test-f03-production-migration.py", 1, "f03"),
    Program("continuity", "mcp-server/test/codex-continuity.test.mjs", 2, "node"),
    Program("leads", "mcp-server/test/lead-workspace-pg.test.mjs", 2, "node"),
    Program("rule-authority", "ops/atomic-rule-approval-local-pg-acceptance.py", 1),
    Program("rule-delivery", "ops/rule-delivery-local-pg-acceptance.py", 1),
    Program("engineering-claim", "ops/engineering-claim-local-pg-gate.py", 1),
    Program("engineering-race", "ops/engineering-envelope-race-local-pg-gate.py", 1),
    Program("ownership", "ops/canonical-ownership-lease-local-pg-gate.py", 1),
    Program("assurance", "ops/assurance-evidence-acceptance-local-pg-gate.py", 1),
    Program("source-merge", "ops/source-merge-authority-local-pg-gate.py", 1),
    Program("calendar", "ops/calendar-canary-local-pg-acceptance.py", 2),
    Program("nightly", "ops/nightly-canary-local-pg-acceptance.py", 2),
    Program("renewal-ingress", "ops/renewal-signed-ingress-local-pg-acceptance.py", 2),
    Program("renewal-lease", "ops/renewal-lease-ledger-local-pg-gate.py", 2),
    Program("incident", "ops/incident-recovery-local-pg-acceptance.py", 2),
    Program("completion", "ops/completion-register-schema-local-pg-gate.py", 2),
    Program("snapshot", "bin/schema-snapshot.sh", 1, "snapshot"),
)
MANIFEST_SHA256 = hashlib.sha256(json.dumps(
    [asdict(p) for p in PROGRAMS], sort_keys=True, separators=(",", ":")
).encode()).hexdigest()


def select_programs(shard):
    if type(shard) is not int or shard not in (0, 1, 2):
        raise ValueError("only the serial manifest and two audited shards are supported")
    return tuple(p for p in PROGRAMS if shard == 0 or p.shard == shard)


def make_report(*, shard, source, toolchain, started, finished, queued,
                setup_seconds, tests, cleanup, returncode, port, root):
    return {
        "schema": "carr-db-shard-shadow/v1", "shard": shard,
        "source": source, "toolchain": toolchain,
        "manifest_sha256": MANIFEST_SHA256,
        "started": started, "finished": finished, "queued": queued,
        "setup_seconds": setup_seconds,
        "tests": [dict(test, returncode=_report_returncode(test["returncode"])) for test in tests],
        "cleanup": cleanup, "returncode": _report_returncode(returncode), "port": port,
        "root": hashlib.sha256(str(root).encode()).hexdigest(),
    }


def _report_returncode(value):
    # subprocess uses -signal; the bounded report uses the shell's 128+signal.
    return 128 - value if type(value) is int and value < 0 else value


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def validate_report(report, *, complete=False):
    fields = {"schema", "shard", "source", "toolchain", "manifest_sha256", "started",
              "finished", "queued", "setup_seconds", "tests", "cleanup", "returncode", "port", "root"}
    if not isinstance(report, dict) or set(report) != fields:
        raise ValueError("report has missing or unknown fields")
    if report["schema"] != "carr-db-shard-shadow/v1" or report["manifest_sha256"] != MANIFEST_SHA256:
        raise ValueError("report schema or manifest mismatch")
    expected = [p.id for p in select_programs(report["shard"])]
    source = report["source"]
    if (not isinstance(source, dict) or set(source) != {"head", "tree"}
            or not all(_hex(v, 40) for v in source.values())):
        raise ValueError("report lacks exact source binding")
    tools = report["toolchain"]
    if (not isinstance(tools, dict) or set(tools) != {"postgres", "python", "node"}
            or not all(isinstance(v, str) and 0 < len(v) < 40
                       and all(c in "0123456789.v" for c in v) for v in tools.values())):
        raise ValueError("report lacks bounded toolchain versions")
    for key in ("started", "finished", "queued", "setup_seconds"):
        if not _number(report[key]):
            raise ValueError("report timing is invalid")
    if not report["queued"] <= report["started"] <= report["finished"]:
        raise ValueError("report timing order is invalid")
    if report["setup_seconds"] > report["finished"] - report["started"]:
        raise ValueError("report setup exceeds duration")
    if type(report["cleanup"]) is not bool or type(report["returncode"]) is not int or not 0 <= report["returncode"] <= 255:
        raise ValueError("report terminal acknowledgement is invalid")
    if type(report["port"]) is not int or not 1024 <= report["port"] <= 65535 or not _hex(report["root"], 64):
        raise ValueError("report isolation identity is invalid")
    tests = report["tests"]
    if not isinstance(tests, list):
        raise ValueError("report tests must be an array")
    ids = []
    for test in tests:
        if (not isinstance(test, dict) or set(test) != {"id", "returncode", "seconds"}
                or test["id"] not in expected or type(test["returncode"]) is not int
                or not 0 <= test["returncode"] <= 255 or not _number(test["seconds"])):
            raise ValueError("report program result is invalid")
        ids.append(test["id"])
    # Incomplete failure reports are retained for diagnosis, never accepted as
    # a complete aggregate. Fail-fast successors can only form a serial prefix.
    if ids != expected[:len(ids)] or (complete and ids != expected):
        raise ValueError("report inventory is partial, reordered or duplicated")
    if sum(t["seconds"] for t in tests) + report["setup_seconds"] > report["finished"] - report["started"]:
        raise ValueError("report stage durations exceed measured work")
    if complete and (report["returncode"] != 0 or not report["cleanup"]
                     or any(t["returncode"] for t in tests)):
        raise ValueError("report has a failed program or unconfirmed cleanup")


def write_report(path: Path, report):
    validate_report(report)
    # Fixed schema only: no command/stdout/stderr/environment, local path or
    # exception text. Opening exclusively also refuses an old attempt's report.
    with path.open("x", encoding="utf-8") as out:
        out.write(json.dumps(report, sort_keys=True, allow_nan=False) + "\n")


def aggregate(reports, *, expected_head, max_work_ratio):
    if not _number(max_work_ratio) or not 1 <= max_work_ratio <= 2:
        raise ValueError("declare a maximum work ratio between 1 and 2 before the trial")
    if not _hex(expected_head, 40) or len(reports) != 3:
        raise ValueError("aggregate requires exactly serial plus both shard reports")
    for report in reports:
        validate_report(report, complete=True)
    by_shard = {r["shard"]: r for r in reports}
    if set(by_shard) != {0, 1, 2}:
        raise ValueError("aggregate is missing a unique shard report")
    serial, one, two = (by_shard[n] for n in (0, 1, 2))
    if serial["source"]["head"] != expected_head or any(
        r["source"] != serial["source"] or r["toolchain"] != serial["toolchain"]
        or r["queued"] != serial["queued"] for r in (one, two)
    ):
        raise ValueError("aggregate source, toolchain or trial queue mismatch")
    if len({r["port"] for r in reports}) != 3 or len({r["root"] for r in reports}) != 3:
        raise ValueError("serial or shard isolation identities collide")
    # Every successful program ran exactly once across the selected manifests.
    sharded_ids = [t["id"] for r in (one, two) for t in r["tests"]]
    if sorted(sharded_ids) != sorted(t["id"] for t in serial["tests"]):
        raise ValueError("shard union differs from serial manifest")
    serial_work = serial["finished"] - serial["started"]
    work = sum(r["finished"] - r["started"] for r in (one, two))
    longest = max(r["finished"] - r["started"] for r in (one, two))
    serial_verdict = serial["finished"] - serial["queued"]
    shard_verdict = max(r["finished"] for r in (one, two)) - serial["queued"]
    accepted = (serial_work > 0 and longest < serial_work and shard_verdict < serial_verdict
                and work <= serial_work * max_work_ratio)
    return {
        "schema": "carr-db-shard-comparison/v1", "accepted": accepted,
        "authorizes_gate_change": False, "source": serial["source"],
        "max_work_ratio": max_work_ratio, "max_failure_rate_delta": 0,
        "observed_failed_programs": 0, "sample_pairs": 1,
        "serial_work_seconds": serial_work, "sharded_work_seconds": work,
        "serial_setup_seconds": serial["setup_seconds"],
        "sharded_setup_seconds": one["setup_seconds"] + two["setup_seconds"],
        "serial_tests_seconds": sum(t["seconds"] for t in serial["tests"]),
        "sharded_tests_seconds": sum(t["seconds"] for r in (one, two) for t in r["tests"]),
        "longest_shard_seconds": longest,
        "serial_queue_to_verdict_seconds": serial_verdict,
        "sharded_queue_to_verdict_seconds": shard_verdict,
        "verdict_boundary": "runner teardown; artifact transfer and aggregate queue excluded",
        "next_action": "retain serial gate; repeat same-source cold/warm trials" if accepted
                       else "reject shard rollout; retain serial gate",
    }


def main():
    import argparse
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--max-work-ratio", type=float, required=True)
    args = parser.parse_args()
    def unique_fields(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON field")
            out[key] = value
        return out
    try:
        paths = sorted(args.reports.rglob("shard-*.json"))
        reports = [json.loads(p.read_text(), object_pairs_hook=unique_fields) for p in paths]
        result = aggregate(reports, expected_head=args.expected_head, max_work_ratio=args.max_work_ratio)
        result["trial_aggregate_queue_to_verdict_seconds"] = time.time() - min(r["queued"] for r in reports)
        result["trial_boundary"] = "paired trial aggregate waits for serial and both shards; never a rollout latency claim"
    except (OSError, ValueError, TypeError, KeyError):
        print("DB shard aggregate refused: missing, invalid, failed or mismatched report; retain serial gate")
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
