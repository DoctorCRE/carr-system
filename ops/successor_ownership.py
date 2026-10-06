#!/usr/bin/env python3
"""One fail-closed ownership policy for successor recovery and approval checks.

Generated ownership requires an exact path and immutable historical JSON. SQL
must be new relative to the branch base and carry its generator marker. Mixed files
retain domain bytes and remove only syntactically bounded bookkeeping.
"""
import json
import re


class OwnershipError(ValueError):
    pass


REGISTRY_JS = re.compile(r"mcp-server/src/scac-mutation-registry(?:\.v[1-9][0-9]*|\.current)?\.generated\.js\Z")
MIGRATION = re.compile(r"migrations/([0-9]+[a-z]?)_(.+\.sql)\Z")
GENERATED_SQL = re.compile(r"migrations/[0-9]+[a-z]?_.+_(?:scac_successor|registry_seal)\.sql\Z")
JSON_ARTIFACTS = frozenset({
    "ops/config/scac-registry-full-entry-set-seals.json",
    "ops/config/scac-registry-chain.json",
    "ops/config/scac-registry-source-inventory-fixtures.v1.json",
})
COUNT_NAMES = r"SCAC_(?:CURRENT_NUMBER|VERSION_COUNT|TOTAL_ENTRY_COUNT|CURRENT_ENTRY_COUNT|CURRENT_SOURCE_COUNT|FULL_SET_SEAL_COUNT)"
ACTIVE_IMPORT = re.compile(r'(?m)^(} from "\./scac-mutation-registry\.v)[1-9][0-9]*(\.generated\.js";)$')


def _generated_json(path, content):
    if content is None:
        return None
    def unambiguous_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise OwnershipError(f"{path}: duplicate generated JSON key {key}")
            value[key] = item
        return value
    try:
        value = json.loads(content, object_pairs_hook=unambiguous_object)
    except (ValueError, UnicodeError) as exc:
        raise OwnershipError(f"{path}: malformed generated JSON") from exc
    if not isinstance(value, dict):
        raise OwnershipError(f"{path}: generated JSON must be an object")
    return value


def validate_outputs(repo, paths):
    """Refuse nonregular sinks and symlink ancestors before any write."""
    import stat
    root = repo.resolve(strict=True)
    for name in paths:
        target = repo / name
        if not target.is_relative_to(repo) or '..' in target.relative_to(repo).parts:
            raise OwnershipError(f"{name}: output escapes staging")
        for part in [target, *target.parents]:
            if part == repo:
                break
            if part.is_symlink():
                raise OwnershipError(f"{name}: symlink output or ancestor")
        if not target.resolve().is_relative_to(root):
            raise OwnershipError(f"{name}: output escapes staging")
        if target.exists() and not stat.S_ISREG(target.stat().st_mode):
            raise OwnershipError(f"{name}: output is not a regular file")


def _same_json(left, right):
    return json.dumps(left, sort_keys=True, separators=(',', ':')) == json.dumps(right, sort_keys=True, separators=(',', ':'))


def is_owned_file(path, before, after):
    """Return whole-file ownership. None means absent, never an unreadable blob."""
    if REGISTRY_JS.fullmatch(path):
        return path in {"mcp-server/src/scac-mutation-registry.generated.js", "mcp-server/src/scac-mutation-registry.current.generated.js"} or before is None and after is not None
    if path in JSON_ARTIFACTS:
        old, new = (_generated_json(path, content) for content in (before, after))
        if old is None or new is None:
            return False
        if path.endswith('scac-registry-chain.json'):
            if len(new.get('versions', [])) != len(old.get('versions', [])) + 1:
                return False
            from pathlib import Path
            import subprocess
            from git_env import scrubbed_env
            script = """import fs from 'node:fs';
import {preservesRegistryChainHistory} from './ops/registry-chain.mjs';
const {before,after}=JSON.parse(fs.readFileSync(0,'utf8'));
process.exit(preservesRegistryChainHistory(before,after)?0:1);
"""
            result = subprocess.run(['node', '--input-type=module', '-e', script],
                input=json.dumps({'before': old, 'after': new}).encode(),
                cwd=Path(__file__).resolve().parents[1], env=scrubbed_env(), capture_output=True, timeout=120)
            return result.returncode == 0
        if path.endswith('full-entry-set-seals.json'):
            if any(key not in new or not _same_json(new[key], value) for key, value in old.items()):
                return False
            numbers = []
            for key, value in new.items():
                match = re.fullmatch(r'scac-mutation-registry.v([1-9][0-9]*)', key)
                if not match or not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
                    return False
                numbers.append(int(match[1]))
            return sorted(numbers) == list(range(1, max(numbers, default=0) + 1))
        if not _same_json({k: v for k, v in old.items() if k != 'patches'}, {k: v for k, v in new.items() if k != 'patches'}):
            return False
        previous, patches = old.get('patches'), new.get('patches')
        if not isinstance(previous, list) or not isinstance(patches, list) or not _same_json(patches[:len(previous)], previous):
            return False
        if len(patches) == len(previous):
            return True
        # Use the canonical decoder to validate the appended counts and digests.
        from pathlib import Path
        import subprocess
        from git_env import scrubbed_env
        script = """import fs from 'node:fs';import {syncBuiltinESMExports} from 'node:module';
const payload=fs.readFileSync(0,'utf8');const read=fs.readFileSync;
fs.readFileSync=(path,...args)=>String(path).endsWith('scac-registry-source-inventory-fixtures.v1.json')?payload:read(path,...args);
syncBuiltinESMExports();
const {frozenInventory,CURRENT_REGISTRY_VERSION}=await import('./ops/scac-mutation-inventory.mjs');
frozenInventory(CURRENT_REGISTRY_VERSION);
"""
        result = subprocess.run(['node', '--input-type=module', '-e', script], input=after,
            cwd=Path(__file__).resolve().parents[1], env=scrubbed_env(), capture_output=True, timeout=120)
        return result.returncode == 0
    if GENERATED_SQL.fullmatch(path) and before is None and after is not None:
        return bool(re.match(rb"\A-- GENERATED by ops/[A-Za-z0-9_.-]+(?:\. Review; never hand-edit\.)?\r?\n", after))
    return False


# Accept the exact seven-line ledger reader, not arbitrary shell containing a
# registry variable. The error arm has no shell expansion or extra command.
_LEDGER = re.compile(
    r'(?m)^[ \t]*(?P<name>[A-Z][A-Z0-9_]*_REGISTRY_APPLIED)="\$\("\$PSQL" -Atqc \\\n'
    r'  "select exists \(select 1 from schema_migrations where filename=\'[0-9]+[a-z]?_[A-Za-z0-9_]+\.sql\'\)" \\\n'
    r'  2>/dev/null\)"\n'
    r'case "\$(?P=name)" in\n'
    r'  t\|f\) ;;\n'
    r'  \*\) echo "schema-snapshot: [A-Za-z0-9 .,:_-]+" >&2; exit 1 ;;\n'
    r'esac\n'
)
_FRONTIER_ASSIGNMENTS = tuple(re.compile(p) for p in (
    rf'{COUNT_NAMES}=[0-9]+',
    r'SCAC_(?:TOTAL_ENTRY_COUNT|CURRENT_ENTRY_COUNT|CURRENT_SOURCE_COUNT)="\$\("\$PSQL" -Atqc "select (?:coalesce\(sum\(entry_count\),0\)|entry_count|source_entry_count) from ops\.scac_mutation_registry_version where registry_version(?:=\'scac-mutation-registry\.v[0-9]+\'| like \'scac-mutation-registry\.v%\')"\)"',
    r'SCAC_CURRENT_RUNTIME="\$REPO/mcp-server/src/scac-mutation-registry\.v[0-9]+\.generated\.js"',
    r'SCAC_CURRENT_CATALOG_FUNCTION="ops\.scac_mutation_catalog_v[0-9]+_current\(\)"',
    r'SCAC_(?:VERSION_ARRAY|HISTORICAL_ARRAY)="(?:\$SCAC_(?:VERSION_ARRAY|HISTORICAL_ARRAY),)?\'scac-mutation-registry\.v[0-9]+\'(?:,\'scac-mutation-registry\.v[0-9]+\')*"',
))
_FRONTIER_IF = re.compile(r'if \[ "\$[A-Z][A-Z0-9_]*_REGISTRY_APPLIED" = t \]; then')


def _snapshot_domain(text):
    text = _LEDGER.sub("", text)
    lines = text.splitlines(keepends=True)
    def owned_assignment(line):
        return any(pattern.fullmatch(line.strip()) for pattern in _FRONTIER_ASSIGNMENTS)
    # Strip complete pure frontier arms, including their paired fi. An arm
    # containing any domain line remains; its recognized pin lines still own
    # themselves, so arbitrary inserted shell cannot hide in an owned region.
    def arm_end(start):
        depth = 1
        for index in range(start + 1, len(lines)):
            value = lines[index].strip()
            if _FRONTIER_IF.fullmatch(value):
                depth += 1
            elif value == "fi":
                depth -= 1
                if depth == 0:
                    return index
            elif not owned_assignment(lines[index]):
                return None
        return None
    result = []
    index = 0
    while index < len(lines):
        if _FRONTIER_IF.fullmatch(lines[index].strip()):
            end = arm_end(index)
            if end is not None:
                index = end + 1
                continue
        if not owned_assignment(lines[index]):
            result.append(lines[index])
        index += 1
    return "".join(result)



def domain_bytes(path, content):
    """Project mixed-file bytes without widening ownership to the whole file."""
    if content is None or b"\0" in content:
        return content
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        return content
    if path == "mcp-server/src/mutation-registry.js":
        text = ACTIVE_IMPORT.sub(r'\g<1><number>\2', text)
    elif path == "bin/schema-snapshot.sh":
        text = _snapshot_domain(text)
    return text.encode("utf-8")
