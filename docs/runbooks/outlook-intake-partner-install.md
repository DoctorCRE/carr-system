# Prepare Dell's MacBook for Outlook intake

Use this runbook for Monday 2026-10-26 in America/Chicago.
**Installation is blocked in this revision.** The pinned decoder cannot prove which account owns calendar items.
The partner installer refuses before it builds a helper, writes identity configuration, or renders a LaunchAgent.
Do not load Joe's existing unconfigured job on Dell's MacBook.

All preparation steps are required. Run commands as Dell, without `sudo`, from a session-owned checkout on his MacBook.
Only Dell can complete steps marked **Dell at keyboard**.
Never copy Joe's credentials, state, or compiled helper to Dell's machine.

1. **Dell at keyboard.** Sign into his macOS account.
2. **Dell at keyboard.** Sign into his own carr.us account in Outlook.
3. Open a session-owned checkout of `DoctorCRE/carr-system` on the MacBook.
4. Create the repository virtual environment.

	```sh
	python3 -m venv .venv
	```

5. Install the repository's locked Python dependencies.

	```sh
	.venv/bin/python -m pip install -r requirements.lock
	```

6. Enter Dell's verified CARR partner principal.

	```sh
	read 'outlook_partner_principal?CARR partner principal: '
	```

7. Enter Dell's own carr.us mailbox address.

	```sh
	read 'outlook_partner_mailbox?Own carr.us mailbox address: '
	```

8. Check Outlook, Command Line Tools, Rust or the prebuilt decoder, and the checkout.

	```sh
	./bin/install-outlook-intake.sh --check --principal "$outlook_partner_principal" --mailbox "$outlook_partner_mailbox"
	```

The check reports `account_scope_unavailable` and exits nonzero with this revision.
Stop here. A successful prerequisite check does not yet exist for the supplied decoder.
If Command Line Tools are missing, Dell must complete the macOS installer opened by `xcode-select --install`.
A prebuilt decoder must match the pinned source, patch, architecture, and build receipt.
An arbitrary executable at the decoder path does not establish compatibility.

## Activate only after account isolation is verified

These steps remain required after a reviewed decoder change makes the prerequisite check pass.
The missing work is account ownership, separate from stable mail identity work WR-000502.
Do not treat a New Outlook UI profile as separate storage without verification.

1. Declare this MacBook as a partner machine.

	```sh
	.venv/bin/python -c 'from lib.machine_role import write_marker; write_marker("partner")'
	```

2. Provision Dell's own CARR machine identity through the existing secure setup process.
3. **Dell at keyboard.** Complete any CARR or model-provider sign-in prompts.
4. Install the intake with the explicit principal and mailbox.

	```sh
	./bin/install-outlook-intake.sh --principal "$outlook_partner_principal" --mailbox "$outlook_partner_mailbox"
	```

5. **Dell at keyboard.** Grant the installed `~/carr-local/bin/outlook-intake-copy` helper Full Disk Access in System Settings.
6. Create Dell's LaunchAgents directory.

	```sh
	mkdir -p "$HOME/Library/LaunchAgents"
	```

7. Install the rendered LaunchAgent.

	```sh
	install -m 644 out/com.carr.outlook-intake.plist "$HOME/Library/LaunchAgents/com.carr.outlook-intake.plist"
	```

8. Load the LaunchAgent in Dell's logged-in GUI session.

	```sh
	launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.carr.outlook-intake.plist"
	```

9. Start one intake run during 07:00-19:30 America/Chicago.

	```sh
	launchctl kickstart "gui/$(id -u)/com.carr.outlook-intake"
	```

10. Enter the approved synthetic test contact's CARR reference.

	```sh
	read 'outlook_fixture_ref?Approved synthetic CARR reference: '
	```

11. Check the completed intake receipt and the synthetic contact's actor and follow-up owner.

	```sh
	.venv/bin/python - "$outlook_partner_principal" "$outlook_fixture_ref" <<'PY'
	import json
	from pathlib import Path
	import subprocess
	import sys
	owner, ref = sys.argv[1:]
	def call(verb, args):
	    result = subprocess.run(['./run.sh', 'call', verb, '-'], input=json.dumps(args),
	                            capture_output=True, text=True, check=True)
	    return json.loads(result.stdout)
	prefix = 'outlook-intake-receipt '
	lines = Path('out/outlook-intake-launchd.log').read_text().splitlines()
	receipts = [json.loads(line.split(prefix, 1)[1]) for line in lines if prefix in line]
	assert receipts and receipts[-1]['state'] == 'succeeded', 'No successful intake receipt'
	action = call('get-next-action', {'ref': ref, 'owner': owner})
	assert action.get('ok') is True and action.get('owner') == owner and action.get('action'), 'No partner follow-up'
	rows = call('catch-me-up', {'ref': ref, 'limit': 30}).get('timeline', [])
	assert any(row.get('entry_kind') == 'activity' and row.get('actor') in (owner, owner + '-local')
	           and row.get('verb') in ('email_in', 'email_out', 'meeting', 'tour', 'call') for row in rows), 'No partner activity'
	print('PASS intake receipt, partner activity, and partner follow-up')
	PY
	```

Use a fresh synthetic contact and input that requires a follow-up.
Verify exclusion of another account through the account-isolation acceptance tests before activation.
A loaded job alone does not prove ingestion. The synthetic contact and its input must be approved test fixtures.
Do not publish mailbox content, private reports, state, or credentials as evidence.
Keep the checkout at its installed path. The helper binds its source profile, output directory, and repository at compile time.
