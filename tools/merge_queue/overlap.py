"""File conflicts plus known shared outputs, derived from the local generators.

Conservative source groups catch digest/registry collisions without pretending to
infer arbitrary cross-file program semantics. New generators must join this list.
"""
from fnmatch import fnmatchcase
from tools.migration_number_contract import SLOT_RE

# Inputs and outputs of tools/regen.py and hooks/gate-integrity.py.
ARTIFACTS = (
    ('migrations/*.sql', 'db/schema.sql', 'ops/migration-shadow.py'),
    ('ops/config/rule-triage.v1.json', 'ops/sync-core-rule-ids.py', 'mcp-server/src/core-rule-ids.js'),
    ('mcp-server/test/fixtures/f03-design-contract-parity.v1.json',
     'mcp-server/test/f03-sql-corpus-generator.mjs', 'mcp-server/src/engineering-runtime.js',
     'mcp-server/test/f03-design-contract-corpus-*-postgres.generated.sql'),
    ('hooks/*.py', 'lib/hook_runtime.py', 'ops/config/gate-baseline.json',
     'ops/config/delegation-gate-hook.json', 'ops/config/hooks.json', 'ops/config/codex-hooks.json',
     'ops/config/rule-enforcement-map.json', 'ops/config/model-floors.json'),
    ('requirements.txt', 'requirements.lock', 'ops/ci-dep-check.py'),
)
# --write-current seals MCP sources only (renderCurrentContracts), even though
# fullInventory also reports scripts/workflows. Keep those independent. If both
# PRs regenerate this registry, their real file sets already share its output.
REGISTRY_OUTPUT = 'mcp-server/src/scac-mutation-registry.current.generated.js'
REGISTRY_INPUTS = ('mcp-server/src/*.js', 'ops/scac-mutation-inventory.mjs')


def overlaps(left, right):
    left, right = set(left), set(right)
    if left & right:
        return True
    def slots(paths):
        return {match[1] for path in paths if path.startswith('migrations/')
                and (match := SLOT_RE.fullmatch(path.removeprefix('migrations/')))}
    if slots(left) & slots(right):
        return True
    def touches(paths, patterns):
        return any(fnmatchcase(path, pattern) for path in paths for pattern in patterns)
    return (any(touches(left, group) and touches(right, group) for group in ARTIFACTS)
            or (REGISTRY_OUTPUT in left and touches(right, REGISTRY_INPUTS))
            or (REGISTRY_OUTPUT in right and touches(left, REGISTRY_INPUTS)))
