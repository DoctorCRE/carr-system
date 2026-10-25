"""Exercise the scheduled refresh shell with synthetic GitHub and git responses."""
from pathlib import Path
import subprocess
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class SchemaRefreshTest(unittest.TestCase):
    def test_refresh_opens_one_ready_snapshot_only_pr_and_starts_checks(self):
        job = yaml.safe_load((ROOT / '.github/workflows/doc-drift.yml').read_text())['jobs']['snapshot']
        self.assertEqual(job['if'], "github.event_name == 'schedule'")
        script = next(step['run'] for step in job['steps'] if step.get('name') == 'Refresh schema snapshot')
        for pending, changed, expected in (
            ('automation/schema-snapshot-older', '1', []), ('unrelated', '0', ['python']),
            ('unrelated', '1', ['python', 'push', 'create', 'ci.yml', 'doc-drift.yml']),
        ):
            with self.subTest(pending=pending, changed=changed):
                prelude = textwrap.dedent(f'''\
                    set -euo pipefail
                    gh() {{
                      case "$1 $2" in
                        'api --paginate') echo {pending};;
                        'pr create') echo create; [[ " $* " != *' --draft '* ]];;
                        'workflow run') echo "$3";;
                        *) return 1;;
                      esac
                    }}
                    python() {{ echo python; }}
                    git() {{
                      case "$1" in
                        diff) return {changed};;
                        add) [[ "$2" == db/schema.sql ]];;
                        push) echo push; [[ " $* " != *--force* ]];;
                        show) echo "5 3 db/schema.sql";;
                      esac
                    }}
                    sleep() {{ :; }}
                    GITHUB_RUN_ID=123
                    RUNNER_TEMP=$(mktemp -d)
                    trap 'rm -rf "$RUNNER_TEMP"' EXIT
                ''')
                result = subprocess.run(['bash', '-c', prelude + script], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), expected)
