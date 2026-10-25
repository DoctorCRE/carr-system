"""Private settings fixtures. No credential file is created or read."""
import json
from pathlib import Path


def write_settings(home: Path, repo: Path, **overrides) -> Path:
    values = dict(owner='fixture-owner', account='owner@example.test', internal_domain='example.test',
                  timezone='America/Chicago', repo=str(repo),
                  profile=str(home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity'),
                  vendor=str(home / 'carr-local/hxstore-reverse-engineering'),
                  copy_helper=str(home / 'carr-local/bin/outlook-intake-copy'),
                  driver=str(home / '.local/bin/cua-driver'),
                  folder_guide=str(home / '.config/carr/outlook-nightly-sweep.folders.md'),
                  send_state=str(home / '.local/state/carr/outlook-send'),
                  mcp_credentials=str(home / '.config/carr/mcp-tokens.env'),
                  database_credentials=str(home / '.config/carr/db.env'),
                  executable_path=f'{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                  deal_prefix='Deals/', vendor_prefix='Networking/Vendors/A.List/',
                  services=['intake', 'refresh', 'triage'], full_screen_height=949)
    values.update(overrides)
    path = home / '.config/carr/outlook.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))
    path.chmod(0o600)
    return path
