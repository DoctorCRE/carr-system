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
    Program("rule-delivery", "ops/rule-delivery-local-pg-acceptance.py"),
    Program("renewal-ingress", "ops/renewal-signed-ingress-local-pg-acceptance.py"),
    Program("incident", "ops/incident-recovery-local-pg-acceptance.py"),
    Program("snapshot", "bin/schema-snapshot.sh", "snapshot"),
)
