"""Interactive Flash preserves complete task context and continues through advisory selection gaps."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO))
spec=importlib.util.spec_from_file_location('prompt_rules',REPO/'tools/flash-prompt-rules.py')
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class PromptRules(unittest.TestCase):
    def test_full_task_and_full_rule_text_reach_context(self):
        flash=module._flash_run()
        task='review '+('task '*1000)
        with patch.object(module,'_flash_run',return_value=flash),patch.object(module,'_log'),patch.object(flash,'pick_rules',return_value=([{'id':'11111111','statement':'Full binding text.'}],None)) as select:
            out=module.build_output(task)
        self.assertEqual(select.call_args.args[0],task.strip())
        self.assertIn('Full binding text.',out['hookSpecificOutput']['additionalContext'])
    def test_known_rules_reach_prompt_despite_selection_gaps(self):
        flash=module._flash_run()
        for gap in ('missing_scope','moment_judgment_unavailable'):
            with self.subTest(gap=gap):
                selection={'rules':[{'id':'11111111','statement':'Full binding text.'}],
                           'complete':False,
                           'data_defects':[{'rule_id':'22222222','reason':gap}] if gap=='missing_scope' else [],
                           'unresolved':[{'rule_id':'22222222','reason':gap}] if gap!='missing_scope' else []}
                with patch.object(module,'_flash_run',return_value=flash),patch.object(module,'_log') as log,patch('lib.rule_scope_lookup.lookup',return_value=selection):
                    out=module.build_output('Read a small local file',judge=lambda *_:None)
                self.assertNotIn('decision',out)
                self.assertIn('Full binding text.',out['hookSpecificOutput']['additionalContext'])
                self.assertIn(gap,log.call_args.args[0]['error'])
    def test_failed_lookup_is_advisory_and_slash_recovery_skips(self):
        flash=module._flash_run()
        with patch.object(module,'_flash_run',return_value=flash),patch.object(module,'_log'),patch.object(flash,'pick_rules',return_value=([],'catalog unavailable')):
            self.assertIsNone(module.build_output('send it'))
            self.assertIsNone(module.build_output('/clear'))
if __name__=='__main__':
    unittest.main()
