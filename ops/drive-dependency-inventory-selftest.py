#!/usr/bin/env python3
"""Hermetic behavior tests for the Drive dependency inventory."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("drive_inventory", ROOT / "ops" / "drive-dependency-inventory.py")
assert SPEC and SPEC.loader
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)

# The one scrubber (ops/git_env.py). git hands every hook a GIT_DIR pointing
# at the repository that invoked it, and on 2026-08-14 that leaked a fixture
# commit onto live main — the throwaway repo built below must not be
# reachable through an inherited GIT_DIR.
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env  # noqa: E402


def registry(*entries: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 1, "contract": "drive-dependencies",
            "binary_exclusions": [{"path": "image.bin", "reason": "synthetic binary"}],
            "entries": list(entries)}


def entry(identifier: str, source: str, *, kind: str = "normal_runtime") -> dict[str, object]:
    return {"id": identifier, "class": kind, "sources": [source], "path_pattern": "{{VAULT}}/**",
            "producer": "fixture", "consumers": ["fixture"], "canonicality": "projection",
            "replacement": {"status": "pending"}}


passed = 0
# A plain system temp dir, no longer rooted under this checkout's own ROOT
# constant: a fixture built INSIDE the live checkout is a fixture a stray
# `git add` in the real repo can reach — the same class of hazard
# ops/selftest-git-isolation-check.py flags as "build their fixture INSIDE
# the checkout".
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    (root / "ops/config").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "ops/launchd").mkdir()
    (root / "pipelines").mkdir()
    (root / "out").mkdir()
    (root / "ops/config/task.json").write_text('{"root":"CARR_VAULT"}\n')
    (root / "ops/launchd/task.plist").write_text('<string>CARR_VAULT</string>\n')
    (root / "bin/run.sh").write_text('out="$CARR_VAULT/00_Context/today.md"\n')
    (root / "pipelines/read.py").write_text('ROOT = "/Users/a/Library/CloudStorage/GoogleDrive-x/My Drive/CARR AI"\n')
    (root / "README.md").write_text('CARR_VAULT is mentioned as prose.\n')
    (root / "extensionless").write_text('CARR_VAULT appears in a future format.\n')
    (root / "config.yaml").write_text('vault: CARR_VAULT\n')
    (root / "utf16.txt").write_text('vault: CARR_VAULT\n', encoding="utf-16")
    (root / "out/runtime.py").write_text('ROOT = "$CARR_VAULT/runtime"\n')
    (root / "image.bin").write_bytes(b"CARR_VAULT\0binary")
    (root / "ops/config/drive-dependencies.schema.v1.json").write_text(
        (ROOT / "ops/config/drive-dependencies.schema.v1.json").read_text())
    # Exercise the production git-ls-files path, rather than only the
    # non-repository fallback used by earlier fixture tests.
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=fixture_env())
    subprocess.run(["git", "-C", str(root), "add", "--all"], check=True, env=fixture_env())
    all_entries = registry(
        entry("task", "ops/config/task.json"), entry("plist", "ops/launchd/task.plist"), entry("shell", "bin/run.sh"),
        entry("python", "pipelines/read.py"), entry("prose", "README.md", kind="prose_only"),
        entry("extensionless", "extensionless", kind="prose_only"), entry("yaml", "config.yaml", kind="prose_only"),
        entry("utf16", "utf16.txt", kind="prose_only"), entry("tracked-out", "out/runtime.py"))
    refs, uncovered, multiple = inventory.audit(root, all_entries)
    assert len(refs) == 9 and any(ref.file == "out/runtime.py" for ref in refs) and not uncovered and not multiple
    passed += 1
    refs, uncovered, multiple = inventory.audit(root, registry(entry("task", "ops/config/task.json")))
    assert {ref.file for ref in uncovered} == {"bin/run.sh", "ops/launchd/task.plist", "pipelines/read.py", "README.md", "extensionless", "config.yaml", "utf16.txt", "out/runtime.py"} and not multiple
    passed += 1
    duplicate = registry(entry("one", "bin/run.sh"), entry("two", "bin/*.sh"))
    refs, uncovered, multiple = inventory.audit(root, duplicate)
    assert any(ref.file == "bin/run.sh" and ids == ["one", "two"] for ref, ids in multiple)
    passed += 1
    # A source row whose file was deleted covers nothing; it must not pass silently.
    try:
        inventory.audit(root, registry(entry("task", "ops/config/task.json"), entry("gone", "pipelines/deleted.py")))
    except inventory.InventoryError as exc:
        assert "gone: pipelines/deleted.py" in str(exc)
        passed += 1
    else:
        raise AssertionError("a source pattern matching no tracked path was accepted")
    try:
        inventory.validate_registry(registry({"id": "bad", "class": "normal_runtime", "sources": ["x"], "path_pattern": "x"}), schema=inventory._read_json(ROOT / "ops/config/drive-dependencies.schema.v1.json"))
    except inventory.InventoryError:
        passed += 1
    else:
        raise AssertionError("operational entry missing replacement fields was accepted")

    weakened_schema = inventory._read_json(ROOT / "ops/config/drive-dependencies.schema.v1.json")
    weakened_schema["$schema"] = "https://example.invalid/not-the-versioned-contract"
    try:
        inventory.validate_registry(all_entries, schema=weakened_schema)
    except inventory.InventoryError:
        passed += 1
    else:
        raise AssertionError("weakened checked-in schema was accepted")

    forbidden_property = registry(entry("root-extra", "bin/run.sh"))
    forbidden_property["unexpected"] = True
    try:
        inventory.validate_registry(forbidden_property, schema=inventory._read_json(ROOT / "ops/config/drive-dependencies.schema.v1.json"))
    except inventory.InventoryError:
        passed += 1
    else:
        raise AssertionError("schema-forbidden root property was accepted")

    wrong_type = registry(entry("bad-type", "bin/run.sh"))
    wrong_entries = wrong_type["entries"]
    assert isinstance(wrong_entries, list) and isinstance(wrong_entries[0], dict)
    wrong_entries[0]["sources"] = "bin/run.sh"
    try:
        inventory.validate_registry(wrong_type, schema=inventory._read_json(ROOT / "ops/config/drive-dependencies.schema.v1.json"))
    except inventory.InventoryError:
        passed += 1
    else:
        raise AssertionError("schema-forbidden entry property type was accepted")

    # A source match cannot swallow a different Drive alias; selectors bind a
    # specific reference when a file has separate semantic dependencies.
    selected = entry("selected", "bin/run.sh")
    selected["reference_selectors"] = [{"source": "bin/run.sh", "alias": "{{VAULT}}", "line": 1}]
    refs, uncovered, multiple = inventory.audit(root, registry(selected))
    assert any(ref.file == "bin/run.sh" for ref in refs) and not multiple
    passed += 1

    (root / "bin/routes.sh").write_text(
        'good="${CARR_VAULT}/allowed/item"\nbad="${CARR_VAULT}/forbidden/item"\n')
    subprocess.run(["git", "-C", str(root), "add", "bin/routes.sh"], check=True, env=fixture_env())
    allowed = entry("allowed-only", "bin/routes.sh")
    allowed["path_pattern"] = "{{VAULT}}/allowed/**"
    allowed["reference_selectors"] = [{"source": "bin/routes.sh", "alias": "{{VAULT}}"}]
    refs, uncovered, multiple = inventory.audit(root, registry(allowed))
    assert any(ref.resolved_path == "{{VAULT}}/allowed/item" for ref in refs)
    assert [ref.resolved_path for ref in uncovered if ref.file == "bin/routes.sh"] == ["{{VAULT}}/forbidden/item"]
    assert not multiple
    passed += 1

    root_only = entry("root-only", "bin/routes.sh")
    root_only["path_pattern"] = "{{VAULT}}"
    refs, uncovered, multiple = inventory.audit(root, registry(root_only))
    assert [ref.resolved_path for ref in uncovered if ref.file == "bin/routes.sh"] == [
        "{{VAULT}}/allowed/item", "{{VAULT}}/forbidden/item"]
    assert not multiple
    passed += 1

    try:
        (root / "mystery.dat").write_bytes(b"CARR_VAULT\0not-declared-binary")
        subprocess.run(["git", "-C", str(root), "add", "mystery.dat"], check=True, env=fixture_env())
        inventory.audit(root, all_entries)
    except inventory.InventoryError as exc:
        assert "ambiguous NUL-bearing" in str(exc)
        passed += 1
    else:
        raise AssertionError("unclassified NUL-bearing tracked file was skipped")

    with patch.object(inventory, "_tracked_paths", return_value=[(root / "rogue", "160000")]):
        try:
            inventory.scan(root, [])
        except inventory.InventoryError as exc:
            assert "unregistered tracked Gitlink" in str(exc)
            passed += 1
        else:
            raise AssertionError("unregistered mode-160000 Gitlink was skipped")

actual = inventory._read_json(ROOT / "ops/config/drive-dependencies.v1.json")
actual_entries = inventory.validate_registry(
    actual, schema=inventory._read_json(ROOT / "ops/config/drive-dependencies.schema.v1.json"))
by_id = {item["id"]: item["class"] for item in actual_entries}
assert all(by_id[item] in inventory.OPERATIONAL_CLASSES for item in (
    "normal-record-source-vault-relative", "normal-executor-agent-definitions",
    "projection-rule-render-verifiers", "normal-front-door-launch-links",
    "normal-routine-child-vault",
    "scheduled-settings-render"))
passed += 1

print(f"drive dependency inventory selftest: {passed}/14 passed")
