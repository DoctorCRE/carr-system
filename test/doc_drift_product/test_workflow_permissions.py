# Test permissions for the scheduled document drift workflow.
from pathlib import Path
import unittest
import yaml

class ScheduledWriterTest(unittest.TestCase):
    def test_instruction_drift_does_not_create_source_changes_on_schedule(self):
        workflow = yaml.safe_load((Path(__file__).resolve().parents[2] / '.github/workflows/doc-drift.yml').read_text())
        for name, job in workflow['jobs'].items():
            self.assertNotEqual(job.get('permissions', {}).get('contents'), 'write', name)

if __name__ == '__main__':
    unittest.main()
