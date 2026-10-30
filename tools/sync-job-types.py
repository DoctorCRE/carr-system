"""Project workflow names for the Worker. Run after changing job-workflows.v1.yaml."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    jobs = yaml.safe_load((ROOT / 'ops/config/job-workflows.v1.yaml').read_text())['job_types']
    (ROOT / 'mcp-server/src/job-types.v1.json').write_text(json.dumps(list(jobs), indent=2) + '\n')
