#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/../../.." && pwd -P)
plugin_root="$repo_root/plugins/pstack"
codex_skills="${CODEX_HOME:-$HOME/.codex}/skills"

common_dir=$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir)
canonical_root=$(cd -- "$common_dir/.." && pwd -P)
branch=$(git -C "$repo_root" branch --show-current)
if [[ "$repo_root" != "$canonical_root" || "$branch" != main ]]; then
  printf 'Run pstack installation from the canonical main checkout: %s\n' "$canonical_root" >&2
  exit 1
fi

for dependency in claude bun python3; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    printf 'Missing dependency: %s\n' "$dependency" >&2
    exit 1
  fi
done

python3 - "$repo_root" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
market = json.loads((root / '.claude-plugin/marketplace.json').read_text())
plugin = root / 'plugins/pstack'
manifest = json.loads((plugin / '.claude-plugin/plugin.json').read_text())
entries = [item for item in market['plugins'] if item['name'] == 'pstack']
if market['name'] != 'carr-local' or manifest['name'] != 'pstack':
    raise SystemExit('Expected marketplace carr-local and plugin pstack.')
if len(entries) != 1 or (root / entries[0]['source']).resolve() != plugin.resolve():
    raise SystemExit('Marketplace pstack source does not match this checkout.')
for filename in ('PORT.md', 'pstack-models.md', 'skills/poteto-mode/scripts/package.json',
                 'skills/poteto-mode/scripts/bun.lock'):
    if not (plugin / filename).is_file():
        raise SystemExit(f'Missing pstack dependency: {filename}')
for name in ('deslop', 'control-ui', 'control-cli', 'create-skill'):
    if not (plugin / 'skills' / name / 'SKILL.md').is_file():
        raise SystemExit(f'Missing local skill dependency: {name}')
PY

read_marketplace_state() {
claude plugin marketplace list --json | python3 -c '
import json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
entries = [item for item in json.load(sys.stdin) if item["name"] == "carr-local"]
if not entries:
    print("absent")
elif len(entries) != 1:
    raise SystemExit("Multiple carr-local marketplaces reported.")
else:
    item = entries[0]
    source = item.get("source")
    if isinstance(source, dict):
        location = source.get("path")
    else:
        location = item.get("path") or item.get("installLocation")
    if location and Path(location).resolve() == root:
        print("present")
    elif location:
        candidate = Path(location).resolve()
        common = subprocess.run(["git", "-C", str(candidate), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True)
        if common.returncode or Path(common.stdout.strip()).resolve() != root / ".git":
            raise SystemExit("Existing carr-local marketplace belongs to another repository; preserved.")
        print("rebind")
    else:
        raise SystemExit("Existing carr-local marketplace has no source path; preserved.")
' "$repo_root"
}

marketplace_state=$(read_marketplace_state)

link_skills() {
python3 - "$plugin_root/skills" "$codex_skills" "$1" "$common_dir" <<'PY'
import subprocess
import sys
from pathlib import Path

source, destination = map(Path, sys.argv[1:3])
apply = sys.argv[3] == 'apply'
common = Path(sys.argv[4]).resolve()

def old_owned_link(target, skill):
    if not target.is_symlink():
        return False
    old = target.resolve()
    if len(old.parents) < 4:
        return False
    root = old.parents[3]
    if old != root / 'plugins/pstack/skills' / skill.name:
        return False
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            capture_output=True, text=True)
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == common
skills = sorted(path for path in source.iterdir() if (path / 'SKILL.md').is_file())
if not skills:
    raise SystemExit('No top-level pstack skills found.')
plans = []
for skill in skills:
    target = destination / skill.name
    if target.is_symlink() and target.resolve() == skill.resolve():
        plans.append((skill, target, True))
        continue
    if (target.exists() or target.is_symlink()) and not old_owned_link(target, skill):
        target = destination / f'pstack-{skill.name}'
    if target.is_symlink() and target.resolve() == skill.resolve():
        plans.append((skill, target, True))
        continue
    if (target.exists() or target.is_symlink()) and not old_owned_link(target, skill):
        raise SystemExit(f'Both skill names are occupied; preserved: {skill.name}, {target.name}')
    plans.append((skill, target, False))
if apply:
    destination.mkdir(parents=True, exist_ok=True)
    for skill, target, linked in plans:
        if linked:
            print(f'Codex skill already linked: {target.name} -> {skill.resolve()}')
            continue
        if target.name != skill.name:
            print(f'Preserved existing Codex skill: {skill.name}')
        if target.is_symlink():
            if not old_owned_link(target, skill):
                raise SystemExit(f'Codex link changed after preflight; preserved: {target}')
            target.unlink()
        target.symlink_to(skill.resolve(), target_is_directory=True)
        print(f'Codex skill linked: {target.name} -> {skill.resolve()}')
else:
    print(f'Codex link preflight passed for {len(plans)} skills.')
PY
}

link_skills check

if [[ "$marketplace_state" == rebind ]]; then
  claude plugin marketplace remove carr-local
fi
if [[ "$marketplace_state" != present ]]; then
  claude plugin marketplace add "$repo_root" --scope user
else
  printf 'Claude marketplace already points to %s\n' "$repo_root"
fi
claude plugin install pstack@carr-local --scope user

if [[ "$(read_marketplace_state)" != present ]]; then
  printf 'Claude marketplace readback: source is not canonical main; installation incomplete.\n' >&2
  exit 1
fi

installed_plugin=$(claude plugin list --json | python3 -c '
import json, sys
from pathlib import Path
matches = [item for item in json.load(sys.stdin)
           if item.get("id") == "pstack@carr-local" and item.get("scope") == "user"]
if len(matches) != 1 or not matches[0].get("enabled"):
    raise SystemExit("Claude user-scope pstack installation is not enabled.")
path = Path(matches[0]["installPath"]).resolve()
if json.loads((path / ".claude-plugin/plugin.json").read_text())["name"] != "pstack":
    raise SystemExit("Claude installed-plugin manifest does not identify pstack.")
print(path)
')

(
  cd -- "$plugin_root/skills/poteto-mode/scripts"
  bun install --frozen-lockfile
)

if [[ "$installed_plugin" != "$plugin_root" ]]; then
  (
    cd -- "$installed_plugin/skills/poteto-mode/scripts"
    bun install --frozen-lockfile
  )
fi

link_skills apply
printf 'Claude pstack@carr-local verified in user scope at %s\n' "$installed_plugin"
printf 'Claude marketplace and Codex skills use canonical main: %s\n' "$repo_root"
