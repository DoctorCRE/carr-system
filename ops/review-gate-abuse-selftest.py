"""Exercise exact-head approvals against real Git history abuse cases."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_env import fixture_env

spec = importlib.util.spec_from_file_location('gate', Path(__file__).with_name('review-gate.py'))
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

class ReviewHistoryAbuse(unittest.TestCase):
    def test_review_history_abuse_cases(self):
        def mk():
            raw=tempfile.mkdtemp(prefix='review-abuse-');self.addCleanup(shutil.rmtree, raw, ignore_errors=True);root=Path(raw)
            def git(*a,ok=True):
                return subprocess.run(['git','-C',raw,*a],capture_output=True,text=True,env=fixture_env(),check=ok).stdout.strip()
            git('init','-b','main');git('config','user.name','T');git('config','user.email','test@example.com')
            return root,git
        def w(root,git,name,text,msg):
            (root/name).write_text(text);git('add',name);git('commit','-m',msg)
        def scenario(name,builder):
            root,git=mk()
            w(root,git,'base','base\n','base')
            git('checkout','-b','feature')
            w(root,git,'feat','approved\n','feature');old=git('rev-parse','HEAD')
            git('checkout','main');w(root,git,'base','main moved\n','main moved')
            git('branch','-f','mainmoved','main')
            base=git('rev-parse','main')
            git('checkout','feature')
            head,base=builder(root,git,old,base)
            self.assertNotEqual(old, head, name)
            pr = {'head': {'sha': head}, 'changed_files': 1, 'additions': 151, 'deletions': 0}
            review = {'user': {'login': 'reviewer[bot]', 'type': 'Bot'}, 'state': 'APPROVED', 'commit_id': old}
            self.assertFalse(gate.approved(pr, [{'filename': 'feat'}], [review], 'reviewer[bot]'), name)
            self.assertTrue(gate.approved(pr, [{'filename': 'feat'}],
                [{**review, 'commit_id': head}], 'reviewer[bot]'), name)
        def clean(root,git,old,base):
            git('merge','--no-edit','main');return git('rev-parse','HEAD'),base
        scenario('clean main merge',clean)
        def merge_also_changes_owned(root,git,old,base):
            git('merge','--no-commit','--no-ff','main');(root/'feat').write_text('evil\n');git('add','feat');git('commit','-m','merge');return git('rev-parse','HEAD'),base
        scenario('merge commit also edits PR-owned file',merge_also_changes_owned)
        def merge_adds_file(root,git,old,base):
            git('merge','--no-commit','--no-ff','main');(root/'new').write_text('x\n');git('add','new');git('commit','-m','merge');return git('rev-parse','HEAD'),base
        scenario('merge commit adds new file',merge_adds_file)
        def merge_edits_mainfile(root,git,old,base):
            git('merge','--no-commit','--no-ff','main');(root/'base').write_text('tampered\n');git('add','base');git('commit','-m','merge');return git('rev-parse','HEAD'),base
        scenario('evil merge resolution touching main-owned file',merge_edits_mainfile)
        def other_branch(root,git,old,base):
            git('checkout','-b','other','main~1');w(root,git,'other','o\n','other');git('checkout','feature');git('merge','--no-edit','other');return git('rev-parse','HEAD'),base
        scenario('merge of branch other than main',other_branch)
        def reverse_merge(root,git,old,base):
            git('checkout','main');git('merge','--no-ff','--no-edit','feature');h=git('rev-parse','HEAD');return h,base
        scenario('merge of PR into main side (first parent main)',reverse_merge)
        def rebase(root,git,old,base):
            git('rebase','main');return git('rev-parse','HEAD'),base
        scenario('rebase rewriting history',rebase)
        def rebase_merges(root,git,old,base):
            git('rebase','--rebase-merges','main');return git('rev-parse','HEAD'),base
        scenario('rebase --rebase-merges',rebase_merges)
        def octopus(root,git,old,base):
            git('checkout','-b','o2','main~1');w(root,git,'o2','x\n','o2');git('checkout','feature');git('merge','--no-edit','main','o2');return git('rev-parse','HEAD'),base
        scenario('octopus merge',octopus)
        def merge_then_commit(root,git,old,base):
            git('merge','--no-edit','main');w(root,git,'feat','after\n','author');return git('rev-parse','HEAD'),base
        scenario('merge then author commit',merge_then_commit)
        def commit_then_merge(root,git,old,base):
            w(root,git,'feat','after\n','author');git('merge','--no-edit','main');return git('rev-parse','HEAD'),base
        scenario('author commit then merge',commit_then_merge)
        def squashed_merge(root,git,old,base):
            git('merge','--squash','main');git('commit','-m','squash');return git('rev-parse','HEAD'),base
        scenario('squash-style single-parent commit',squashed_merge)
        # stale base: P2 newer than base
        def p2_after_base(root,git,old,base):
            git('checkout','main');w(root,git,'base','main moved again\n','m2');git('checkout','feature');git('merge','--no-edit','main');return git('rev-parse','HEAD'),base
        scenario('P2 not ancestor of recorded base',p2_after_base)
        # reverting main change in merge
        def revert_main(root,git,old,base):
            git('merge','--no-commit','--no-ff','main');(root/'base').write_text('base\n');git('add','base');git('commit','-m','merge');return git('rev-parse','HEAD'),base
        scenario('merge reverts main change',revert_main)

if __name__ == '__main__': unittest.main()
