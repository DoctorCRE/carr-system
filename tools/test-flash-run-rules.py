"""Flash uses the same store-scoped lookup and keeps known rules when selection advice is incomplete."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from contextlib import ExitStack
REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO))
spec=importlib.util.spec_from_file_location('flash_rules',REPO/'tools/flash-run.py')
assert spec is not None and spec.loader is not None
flash=importlib.util.module_from_spec(spec)
spec.loader.exec_module(flash)
class FlashRules(unittest.TestCase):
    def test_full_rules_and_incomplete_lookup_are_distinct(self):
        rule={'id':'11111111','statement':'Read the whole change.','scope':{'moment':'Before review'},'personal_to':None}
        for complete in (True,False):
            with patch('lib.rule_scope_lookup.lookup',return_value={'rules':[rule],'complete':complete}) as lookup:
                rules,note=flash.pick_rules('Review this change',judge=lambda *_:None)
                self.assertEqual(rules,[rule])
                self.assertEqual(note is None,complete)
                self.assertIn('Review this change',lookup.call_args.args[0]['task'])
    def test_scripted_task_continues_with_known_rules_for_selection_gaps(self):
        rule={'id':'11111111','statement':'Read the whole change.'}
        for gap in ('missing_scope','moment_judgment_unavailable'):
            with self.subTest(gap=gap), ExitStack() as stack:
                selection={'rules':[rule],'complete':False,
                           'data_defects':[{'rule_id':'22222222','reason':gap}] if gap=='missing_scope' else [],
                           'unresolved':[{'rule_id':'22222222','reason':gap}] if gap!='missing_scope' else []}
                stack.enter_context(patch('lib.rule_scope_lookup.lookup',return_value=selection))
                stack.enter_context(patch.object(flash.flashlib,'is_switched_off',return_value=False))
                intake=Mock()
                intake.check_ambiguity.return_value={'verdict':'clear'}
                intake.route_task.return_value={'verdict':'local'}
                intake.pick_context.return_value={'paths':[]}
                notebook=Mock()
                notebook.recall_mistakes.return_value={'lines':[]}
                best=Mock()
                best.select_candidate.return_value={'verdict':'one'}
                stack.enter_context(patch.object(flash,'_lib',side_effect=lambda name: {'jev_intake':intake,'jev_notebook':notebook,'jev_best_of':best}[name]))
                stack.enter_context(patch.object(flash,'_tracked_files',return_value=[]))
                stack.enter_context(patch.object(flash,'_read_examples',return_value=[]))
                log=stack.enter_context(patch.object(flash,'_append'))
                say=stack.enter_context(patch.object(flash,'_say'))
                attempt=stack.enter_context(patch.object(flash,'run_attempt',return_value={
                    'id':'one','patch':'synthetic patch','code_or_diff':'synthetic patch',
                    'probe_results':{'patch_lines':1},'test_output':'ok','test_exit_code':0,'elapsed_s':0}))
                args=SimpleNamespace(cwd=str(REPO),task='Read a small local file',test='test',
                    sandbox='off',no_learn=True,force=False,effort='low',no_rules=False,
                    attempts=1,think='off',all_attempts=False,dry_run=True,keep=False)
                self.assertEqual(flash.cmd_run(args),0)
                self.assertIn('Read the whole change.',attempt.call_args.kwargs['rules_text'])
                self.assertIn(gap,log.call_args.args[1]['rules_error'])
                self.assertTrue(any(gap in call.args[0] for call in say.call_args_list))
    def test_unavailable_lookup_is_visible(self):
        with patch('lib.rule_scope_lookup.lookup',side_effect=RuntimeError('secret')):
            rules,note=flash.pick_rules('Review')
        self.assertEqual(rules,[])
        self.assertEqual(note,'rule scope catalog unavailable')
    def test_block_keeps_the_whole_statement(self):
        self.assertIsNone(flash.rules_block([]))
        self.assertIn('Read the whole change.',flash.rules_block([{'id':'11111111','statement':'Read the whole change.'}]))
if __name__=='__main__':
    unittest.main()
