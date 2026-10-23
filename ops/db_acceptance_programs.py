"""Ordered acceptance programs for the disposable Postgres lane."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Program:
    id: str
    path: str
    kind: str = "python"


PROGRAMS = (
    Program("continuity", "mcp-server/test/codex-continuity.test.mjs", "node"),
    Program("leads", "mcp-server/test/lead-workspace-pg.test.mjs", "node"),
    Program("rule-authority", "ops/atomic-rule-approval-local-pg-acceptance.py"),
    Program("rule-delivery", "ops/rule-delivery-local-pg-acceptance.py"),
    Program("calendar", "ops/calendar-canary-local-pg-acceptance.py"),
    Program("nightly", "ops/nightly-canary-local-pg-acceptance.py"),
    Program("renewal-ingress", "ops/renewal-signed-ingress-local-pg-acceptance.py"),
    Program("renewal-lease", "ops/renewal-lease-ledger-local-pg-gate.py"),
    Program("incident", "ops/incident-recovery-local-pg-acceptance.py"),
    Program("snapshot", "bin/schema-snapshot.sh", "snapshot"),
)
