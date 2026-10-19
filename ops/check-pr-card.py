"""Validate PR identity and copy with the board's canonical rules."""
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.progress_hierarchy import metadata_text, pr_binding

event = json.load(sys.stdin)
try:
    body = metadata_text(event['pull_request'].get('body'))
    if re.search(r'^Emergency dispatch:', body, re.M):
        for label, pattern in (
            ('Emergency dispatch', r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}'),
            ('Emergency actor', r'[^\s@]+@[^\s@]+'), ('Emergency reason', r'\S[^\r\n]*')):
            if len(re.findall('^' + label + ': ' + pattern + '$', body, re.M)) != 1:
                raise ValueError(f'PR body requires one valid {label}: line')
        print('Emergency dispatch declared; the local weekly audit retains its actor and reason.')
    else:
        pr_binding(body, event['repository']['full_name'], event['pull_request']['number'], required=True)
except (ValueError, KeyError, TypeError) as error:
    print(str(error), file=sys.stderr)
    sys.exit(1)
