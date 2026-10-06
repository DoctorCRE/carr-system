#!/usr/bin/env python3
"""Static contract test for Dell's zero-interaction Claude migration packet."""

from __future__ import annotations

import os
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "migrate-dell.sh"
CLAUDE = ROOT / "CLAUDE.md"
CONFIG_AS_CODE = ROOT / "ops" / "config-as-code.py"

script = SCRIPT.read_text(encoding="utf-8")
claude_boot = CLAUDE.read_text(encoding="utf-8")
packet = json.loads((ROOT / "ops/config/task-boot/dell-migration.json").read_text())
assert "ops/config/task-boot/dell-migration.json" in claude_boot
claude = packet["instructions"]
config_as_code = CONFIG_AS_CODE.read_text(encoding="utf-8")
combined = script + "\n" + claude
flat_script = re.sub(r"\s+", " ", script)
flat_claude = re.sub(r"\s+", " ", claude)
flat_combined = re.sub(r"\s+", " ", combined)
failures: list[str] = []


def require(name: str, condition: bool) -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}")
        failures.append(name)


syntax = subprocess.run(
    ["/bin/zsh", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
)
require("migration shell parses", syntax.returncode == 0)
require("migration script is executable", os.access(SCRIPT, os.X_OK))

require(
    "exact phrase is a Claude takeover trigger",
    "ready for migration" in flat_claude.lower()
    and "explicit authorization" in flat_claude
    and "without asking follow-up questions" in flat_claude,
)
require(
    "Claude runs the one bounded command itself",
    "./bin/migrate-dell.sh --apply </dev/null" in claude
    and "Do not hand the\n   command back to the human" in claude,
)
require(
    "failure cannot close migration records",
    "A nonzero exit is a visible blocker" in claude
    and "Do not close any migration record after a failed run" in claude,
)
require(
    "success requires the exact machine receipt status",
    combined.count("machine_migrated_pending_record_closeout") >= 3
    and "dell-migration-receipt.json" in combined
    and '"exit_code"' in script
    and '"source_commit"' in script
    and '"record_closeout"' in script,
)
require(
    "receipt is written atomically",
    'tmp="$RECEIPT.tmp.$$"' in script and 'mv -f "$tmp" "$RECEIPT"' in script,
)
require(
    "zsh receipt does not shadow its read-only status parameter",
    'local receipt_status="migration_incomplete"' in script
    and '"$tmp" "$receipt_status" "$commit"' in script
    and 'local status=' not in script,
)
def _receipt_writer_parses() -> bool:
    """Run the receipt's embedded Python writer and parse what it writes."""
    import json
    import tempfile
    match = re.search(r"/usr/bin/python3 -c '\n(.*?)\n' \"\$tmp\"", script, re.S)
    if not match:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "receipt.json"
        run = subprocess.run(
            [sys.executable, "-c", match.group(1), str(out),
             "machine_migrated_pending_record_closeout", "abc123", "main", "0", "19", "1", "0"],
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            return False
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            return False
        return data.get("status") == "machine_migrated_pending_record_closeout"


require("receipt the script writes is valid JSON a reader can parse", _receipt_writer_parses())

requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
require(
    "type checking is a Python-gated dev dependency, not a Dell runtime dependency",
    'mypy>=2.3; python_version >= "3.10"' in requirements
    and "Development/nightly static checking only" in requirements
    and "mypy>=1.13" not in requirements,
)
require(
    "record closeout is exact",
    all(token in combined for token in (
        "fa0e6c92-8bc7-4e42-9970-0402914d6a19",
        "deb4357e-801f-49f6-bc6c-4c884e3e1f7c",
        '"required_after_success": ["A15", "A17"]',
        '"must_remain_open": ["A11", "A12", "A13", "A16"]',
    )),
)
require(
    "live Dell identity and render parity are mandatory",
    "server-derived sponsor is Dell" in flat_combined
    and "dell-personal" in flat_combined
    and "counts match the current generated" in flat_combined,
)
require(
    "caller cannot select partner tenant or capability",
    "Do not pass a partner, tenant, or capability selector" in flat_script
    and "Never choose a partner, tenant, or capability through a caller-supplied argument" in flat_claude,
)
require(
    "fallback remains supported through cutoff",
    "2026-08-21 cutoff" in flat_combined
    and "does not retire them early" in flat_script,
)
require(
    "connector reconnect is not guessed",
    "Do not disconnect or reconnect" in flat_script
    and "Never reconnect merely as a guess" in flat_claude,
)
require(
    "Claude security is not widened",
    "does not waive Claude Code's own tool security, widen permissions" in claude
    and "all other settings keys preserved" in script,
)

active_lines = [
    line for line in script.splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
require(
    "no sudo command exists",
    not any(re.match(r"^\s*sudo(?:\s|$)", line) for line in active_lines),
)
require(
    "no interactive shell read exists",
    not any(re.match(r"^\s*read(?:\s|$)", line) for line in active_lines),
)
require(
    "no raw git pull exists",
    not any(re.search(r"\bgit\s+(?:-C\s+\S+\s+)?pull\b", line) for line in active_lines),
)
require(
    "preflight executes this selftest from the pulled packet",
    "context-handoff-gate corpus-render-gate config-as-code migrate-dell" in script,
)
require(
    "fresh-machine preflight exercises the actual Claude-only installer",
    "actual installer, with empty Claude settings and no Codex client" in script
    and "HOME=\"$TMP/home\"" in script
    and "python3 ops/config-as-code.py install" in script
    and "Claude-only config install dry run" in script,
)
require(
    "Dell's missing Codex client is an explicit supported state",
    "Dell's launch machine is Claude-only" in flat_claude
    and "complete absence of `~/.codex` is an expected supported state" in flat_claude
    and "partial Codex state fails visibly" in flat_claude,
)
require(
    "fresh Claude settings are created rather than contradicted",
    'raw = read(SETTINGS) if settings_existed else "{}"' in config_as_code
    and "Claude settings: WILL BE CREATED" in config_as_code,
)
require(
    "LaunchAgent install or reload failure cannot report migration success",
    "launchd_activation_failures.append" in config_as_code
    and "ERROR: LaunchAgent install/reload failed" in config_as_code,
)
require(
    "migration dry run propagates installer failure",
    "hook install dry run failed" in script
    and 'if $PY "$REPO/ops/config-as-code.py" install </dev/null >/tmp/mig-hooks.log 2>&1; then' in script,
)
require(
    "preflight defaults to origin main with an explicit commit-only test override",
    'PREFLIGHT_REF="${CARR_PREFLIGHT_REF:-origin/main}"' in script
    and '"$PREFLIGHT_REF^{commit}"' in script,
)
require(
    "scratch preflight proves the exact selected commit was fetched and checked out",
    'fetch --quiet "$REPO" "$PREFLIGHT_REF"' in script
    and 'if [ "$FETCHED" != "$REF" ]' in script
    and 'if [ "$CHECKED_OUT" != "$REF" ]' in script
    and 'checkout --quiet --detach "$REF"' in script,
)
require(
    "every executable-mode preflight failure increments the result",
    script.index("PFAIL=0") < script.index('MODE=$(git -C "$TMP/home/carr-system"')
    and 'PFAIL=$((PFAIL+1))' in script[
        script.index('MODE=$(git -C "$TMP/home/carr-system"'):
        script.index('say "  gates, as Dell will run them:"')
    ],
)

if failures:
    print(f"\nFAILURES: {', '.join(failures)}")
    sys.exit(1)

print("\nmigrate-dell selftest: all takeover, safety, receipt, and closeout checks passed")
