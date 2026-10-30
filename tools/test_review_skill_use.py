import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from review_skill_use import findings


class SkillUse(unittest.TestCase):
    def test_review_findings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / 'rollout-test.jsonl'
            brief = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Card: WR594.S3.J1\nType: feature-backend\nSkills: all'}]}}
            def row(name):
                return {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command', 'call_id': name, 'arguments': json.dumps({'cmd': f'cat /skills/{name}/SKILL.md'})}}
            skills = ['poteto-mode', 'tdd', 'principle-prove-it-works']
            def write(names):
                log.write_text('\n'.join(json.dumps(x) for x in [brief, *(item for n in names for item in (row(n), {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': n, 'output': 'Exit code: 0\n# Skill'}}))]))
            write(skills)
            self.assertEqual(findings('Card: WR594.S3.J1', root), [])
            write(skills[:-1])
            self.assertEqual(findings('Card: WR594.S3.J1', root), ['Required skill did not run: pstack:principle-prove-it-works'])
            log.unlink()
            self.assertEqual(findings('Card: WR594.S3.J1', root), ['Build session log missing for Card WR594.S3.J1'])
            brief['payload']['content'][0]['text'] = 'Card: WR594.S3.J1\nType: unknown'
            write(skills)
            self.assertEqual(findings('Card: WR594.S3.J1', root), ['Unknown job type: unknown'])

if __name__ == '__main__':
    unittest.main()
