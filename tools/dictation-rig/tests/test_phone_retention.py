"""Run the existing retention command against synthetic imported recordings."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'


class PhoneRetentionTests(unittest.TestCase):
    def test_only_verified_final_post_grace_sessions_lose_audio_and_transcripts(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / 'Library/Logs').mkdir(parents=True)
            now = datetime.now(timezone.utc)
            files = ('imported.audio', 'imported.wav', 'imported.m4a', 'imported.mp3', 'imported.mp4',
                     'imported.webm', 'mic.caf', 'system.caf', 'transcript.json', 'transcript.md')
            for index, scenario in enumerate(('eligible', 'grace', 'unfiled', 'pending', 'unverified', 'legacy-marker', 'bad-time')):
                session = home / 'Recordings' / f'phone-10000000-0000-0000-0000-{index:012d}'
                session.mkdir(parents=True)
                for name in files:
                    (session / name).write_text('synthetic fixture')
                (session / 'announcement.json').write_text('{}')
                (session / 'meta.json').write_text('{}')
                marker = {'post_call': True, 'aggregate_report_hash': 'a' * 64,
                          'backend_report_sha256': 'b' * 64, 'pending_items': 0,
                          'ingested_at': (now - timedelta(hours=73)).isoformat()}
                if scenario == 'grace':
                    marker['ingested_at'] = (now - timedelta(hours=71)).isoformat()
                if scenario == 'pending':
                    marker['pending_items'] = 1
                if scenario == 'unverified':
                    marker['backend_report_sha256'] = ''
                if scenario == 'legacy-marker':
                    marker = {'meeting_record': 'synthetic', 'ingested_at': marker['ingested_at']}
                if scenario == 'bad-time':
                    marker['ingested_at'] = 'NaN'
                if scenario != 'unfiled':
                    (session / 'ingested.json').write_text(json.dumps(marker))
            subprocess.run(['/bin/sh', str(BIN / 'purge-recordings.sh')], check=True,
                           env={'HOME': str(home), 'PATH': os.environ['PATH']})
            for index, scenario in enumerate(('eligible', 'grace', 'unfiled', 'pending', 'unverified', 'legacy-marker', 'bad-time')):
                session = home / 'Recordings' / f'phone-10000000-0000-0000-0000-{index:012d}'
                with self.subTest(scenario=scenario):
                    for name in files:
                        self.assertEqual((session / name).exists(), scenario != 'eligible', name)
                    self.assertTrue((session / 'announcement.json').exists())
                    self.assertTrue((session / 'meta.json').exists())


if __name__ == '__main__':
    unittest.main()
