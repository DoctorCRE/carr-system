"""Flash uses the same store-scoped lookup and refuses incomplete advice."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
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
                self.assertEqual(rules,[rule] if complete else [])
                self.assertEqual(note is None,complete)
                self.assertIn('Review this change',lookup.call_args.args[0]['task'])
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
