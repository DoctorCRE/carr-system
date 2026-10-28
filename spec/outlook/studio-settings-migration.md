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
print(json.dumps({
    'owner': 'joe',
    'account': 'joe.bookout@carr.us',
    'internal_domain': 'carr.us',
    'timezone': 'America/Chicago',
    'repo': str(home / 'carr-local/outlook-repo'),
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
There are no owner, address, profile or credential fallbacks.
Optional `signing_identity` names this Mac's code-signing certificate. Omit it for ad-hoc signing.

| Fields | Type and meaning |
| --- | --- |
| `owner` | Lowercase record-layer owner principal. It does not assert authenticated identity. |
| `account` | Lowercase mailbox address used for window and Sent Items checks. |
| `internal_domain` | Lowercase domain used for internal email matching. |
| `timezone` | IANA timezone used for local windows, reports and the daily send limit. |
| `repo` | Absolute path of the checkout the Outlook jobs run from, canonicalized when loaded. Only `bin/install-outlook-intake.sh` creates or moves it, so pulling the canonical checkout ships no Outlook code. |
| `profile` | Absolute Outlook profile directory. Only its HxStore and EFMData files enter the private snapshot. |
| `vendor` | Absolute decoder build directory. The installer builds `vendor/hxprobe` with Cargo's target directory at `build/` inside it and installs the verified decoder at `hxprobe/target/release/hxprobe`. |
| `copy_helper`, `driver`, `folder_guide`, `send_state` | Absolute paths to the granted helper, local UI driver, private folder guide and send ledger directory. |
| `mcp_credentials`, `database_credentials` | Absolute file pointers outside the repository. They hold no credential values in settings. |
| `executable_path` | Executable search path rendered into the three Outlook plists. |
| `deal_prefix`, `vendor_prefix` | Folder matching prefixes, each ending in `/`. |
| `full_screen_height` | Positive integer in screen points used to refuse full-screen mailbox windows. |
| `services` | Explicit enabled local services from `intake`, `refresh`, and `triage`. |

Outlook uses the configured MCP file with the existing local client profile. Ambient MCP token and break-glass overrides are removed for this route.
The exporter uses the configured database credential file. Its other callers retain their existing credential selection behavior.
The shared services catalog retains its declared owner. Outlook jobs use the private settings owner for their record actions. Registry sync never reads per-machine settings; primary deployment placements stay unchanged.

## Keep Full Disk Access across helper rebuilds

Complete all steps on each Mac. Keep the same certificate and helper path for later rebuilds.
The certificate is local to that Mac. Do not commit its name or export its private key.
Without `signing_identity`, the installer uses ad-hoc signing and warns that rebuilds need a Full Disk Access re-grant.
The installer rejects a configured identity that cannot sign or produces a cdhash designated requirement.

1. Open Keychain Access on the intake Mac.
2. Select **Keychain Access > Certificate Assistant > Create a Certificate**.
3. Create one self-signed root certificate with **Certificate Type > Code Signing** and a unique name for this Mac.
4. Set `signing_identity` in `~/.config/carr/outlook.json` to that certificate's exact name.
5. Run `./bin/install-outlook-intake.sh` from the source checkout.
6. Re-grant the configured `copy_helper` Full Disk Access in **System Settings > Privacy & Security > Full Disk Access**.
7. Delete the configured helper's `.pin` file to request one deliberate rebuild.
8. Run `./bin/install-outlook-intake.sh` again.
9. Confirm the rebuilt helper retains Full Disk Access without another grant through the next successful scheduled intake copy.

For step 7, `.pin` replaces the helper's filename suffix. For `outlook-intake-copy`, the pin is `outlook-intake-copy.pin`.
Do not create another certificate for the rebuild check. Certificate replacement changes the helper's identity.
