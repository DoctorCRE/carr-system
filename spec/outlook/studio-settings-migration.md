# Write the Studio's Outlook settings

The orchestrator runs this after merge from the Studio's canonical checkout, before the intake installer.
This command writes and validates one private settings file. It does not install or load a service.
It refuses to overwrite an existing file. It never reads the credential files.

All commands below are required. The final command proves that the settings are valid.

```sh
cd ~/carr-system
python3 - <<'PY' | .venv/bin/python -m lib.outlook_settings write
import json
from pathlib import Path

home = Path.home()
repo = Path.cwd().resolve()
print(json.dumps({
    'owner': 'joe',
    'account': 'joe.bookout@carr.us',
    'internal_domain': 'carr.us',
    'timezone': 'America/Chicago',
    'repo': str(repo),
    'profile': str(home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity'),
    'vendor': str(home / 'carr-local/hxstore-reverse-engineering'),
    'copy_helper': str(home / 'carr-local/bin/outlook-intake-copy'),
    'driver': str(home / '.local/bin/cua-driver'),
    'folder_guide': str(home / '.config/carr/outlook-nightly-sweep.folders.md'),
    'send_state': str(home / '.local/state/carr/outlook-send'),
    'mcp_credentials': str(home / '.config/carr/mcp-tokens.env'),
    'database_credentials': str(home / '.config/carr/db.env'),
    'executable_path': f'{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
    'deal_prefix': 'Deals/',
    'vendor_prefix': 'Networking/Vendors/A.List/',
    'services': ['intake', 'refresh', 'triage'],
    'full_screen_height': 949
}))
PY
.venv/bin/python -m lib.outlook_settings validate
```

Expected output ends with `outlook settings valid:` and the file's absolute path.
The file is `~/.config/carr/outlook.json`, owned by the current user, mode `0600`.
These identity and path values reproduce the previous Studio source values.
The account address comes from the prior draft script's documented account identity; this change never probes the live mailbox.

The orchestrator then uses the existing intake installer. It rebuilds the dedicated helper once for its new scope-readback operation.
The granted binary still binds its source profile, output root and intake command at compile time.
A settings change cannot redirect that binary. Runtime scope readback refuses mismatched settings until the installer rebuilds it.
The installer preserves the unchanged helper on subsequent runs. Native Full Disk Access validation remains the orchestrator's installation check.

The Studio file enables intake, refresh and triage, matching the current primary placement from PR #2236.
No installer runs as part of this source change. The later installation for another person belongs in a separate PR.

## Settings schema

Every field in the command is required. Unknown or duplicate fields are rejected.
There are no identity, address, profile or credential fallbacks.

| Fields | Type and meaning |
| --- | --- |
| `owner` | Lowercase record-layer owner principal. It does not assert authenticated identity. |
| `account` | Lowercase mailbox address used for window and Sent Items checks. |
| `internal_domain` | Lowercase domain used for internal email matching. |
| `timezone` | IANA timezone used for local windows, reports and the daily send limit. |
| `repo` | Absolute checkout path, canonicalized when loaded. |
| `profile` | Absolute Outlook profile directory. Only its HxStore and EFMData files enter the private snapshot. |
| `vendor` | Absolute pinned decoder checkout. The decoder lives at `hxprobe/target/release/hxprobe` inside it. |
| `copy_helper`, `driver`, `folder_guide`, `send_state` | Absolute paths to the granted helper, local UI driver, private folder guide and send ledger directory. |
| `mcp_credentials`, `database_credentials` | Absolute file pointers outside the repository. They hold no credential values in settings. |
| `executable_path` | Executable search path rendered into the three Outlook plists. |
| `deal_prefix`, `vendor_prefix` | Folder matching prefixes, each ending in `/`. |
| `full_screen_height` | Positive integer in screen points used to refuse full-screen mailbox windows. |
| `services` | Explicit enabled local services from `intake`, `refresh`, and `triage`. |

Outlook uses the configured MCP file with the existing local client profile. Ambient MCP token and break-glass overrides are removed for this route.
The exporter uses the configured database credential file. Its other callers retain their existing credential selection behavior.
The shared services catalog retains its declared owner. Outlook jobs use the private settings owner for their record actions. Registry sync never reads per-machine settings; primary deployment placements stay unchanged.
