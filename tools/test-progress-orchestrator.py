import json
import unittest
from progress_orchestrator import project


class OrchestratorTests(unittest.TestCase):
    def test_review_return_merge_and_release_are_distinct_facts(self):
        queue = {'revision': 3, 'items': [{'card_id': 'q1', 'title': 'Deal room search', 'stage': 'fixing',
            'desk': 'cx-app2', 'prs': [{'repo': 'jbookout/doctorcre-app', 'number': 12}],
            'last_event': {'at': '2026-10-21T19:00:00Z', 'reason': 'Review requested keyboard fixes'}}]}
        state = {'tasks': {'p': {'title': 'Deal room search', 'repo': 'jbookout/doctorcre-app', 'pr': 12,
            'stage': 'review', 'pr_phase': 'Ready to merge'}}}
        result = project(state, queue, observed_at='2026-10-21T19:01:00Z')
        self.assertEqual(result['cards'][0]['state'], 'fixing')
        self.assertEqual(len(result['cards']), 1)
        self.assertEqual(result['cards'][0]['owner'], 'cx-app2')
        state['tasks']['p'].update(stage='merged', pr_phase='Merged', merge_sha='a'*40)
        self.assertEqual(project(state, queue)['cards'][0]['state'], 'merged')
        state['tasks']['p']['stage'] = 'live'
        state['tasks']['p']['evidence'] = 'Matching app release contains the merge; consumer exercise passed'
        self.assertEqual(project(state, queue)['history'][0]['delivery_evidence'], state['tasks']['p']['evidence'])
        self.assertEqual(project(state, queue)['history'][0]['state'], 'live')

    def test_projection_excludes_private_payloads_and_keeps_unknown_explicit(self):
        queue = {'revision': 4, 'items': [{'card_id': 'one', 'title': 'Calendar availability',
            'stage': 'done', 'task': 'RAW BRIEF', 'description': 'PRIVATE DESCRIPTION',
            'result_detail': 'RAW RESULT', 'claim': 'SECRET CLAIM', 'owner': '/Users/private',
            'last_event': {'reason': 'contact person@example.com', 'at': '2026-10-21T19:00:00Z'}}]}
        result = project({'tasks': {}}, queue)
        encoded = json.dumps(result)
        for private in ('RAW BRIEF', 'PRIVATE DESCRIPTION', 'RAW RESULT', 'SECRET CLAIM', '/Users/private', 'person@example.com'):
            self.assertNotIn(private, encoded)
        self.assertEqual(result['history'][0]['state'], 'review')
        self.assertEqual(result['history'][0]['owner'], 'Not recorded')
        self.assertEqual(result['sources']['github']['state'], 'unknown')

    def test_portfolio_contains_backend_and_app_and_bounds_completed_history(self):
        tasks = {str(i): {'title': f'Feature {i}', 'stage': 'live', 'repo': 'jbookout/carr-system'} for i in range(100)}
        tasks['active'] = {'title': 'Shared search contract', 'stage': 'build', 'repo': 'jbookout/carr-system'}
        result = project({'tasks': tasks}, None, portfolio={'tasks': {'app': {'title': 'Deal room search', 'stage': 'review', 'repo': 'jbookout/doctorcre-app'}}})
        self.assertEqual(len(result['history']), 20)
        self.assertEqual(len(result['cards']), 2)
        self.assertEqual(result['sources']['queue']['state'], 'unavailable')

class PublicationTests(unittest.TestCase):
    def test_queue_publication_whitelists_metadata(self):
        import tempfile
        from pathlib import Path
        import sys
        sys.path.insert(0, str(Path(__file__).parent / 'room-bridge'))
        from work_queue import WorkQueue, board_projection
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'queue.sqlite3'
            with WorkQueue(db) as q:
                q.enqueue('safe', desk='cx-app2', task='PRIVATE BRIEF', actor='test',
                    title='Deal room search', description='PRIVATE DESCRIPTION')
                card=q.claim(eligible_desks={'cx-app2'}, actor='runner', owner={})
                q.finish('safe',card['claim'],{'status':'completed','detail':'PRIVATE RESULT'},actor='worker')
            encoded=json.dumps(board_projection(db))
            for private in ('PRIVATE BRIEF','PRIVATE DESCRIPTION','PRIVATE RESULT',card['claim']):
                self.assertNotIn(private,encoded)

    def test_scheduled_refresh_is_bounded_to_one_minute_without_a_model(self):
        import plistlib
        from pathlib import Path
        config=plistlib.loads((Path(__file__).parents[1]/'ops/launchd/local.carr-progress-board.plist').read_bytes())
        self.assertEqual(len(config['StartCalendarInterval']),60)
        self.assertEqual(config['ProgramArguments'][1],'{{REPO}}/ops/progress-board-render.sh')

    def test_source_outage_preserves_the_last_successful_queue_observation(self):
        from progress_orchestrator import retain_unavailable_queue
        previous={'cards':[{'id':'work','origin':'queue','prs':[],'title':'Search'}], 'history':[],
                  'sources':{'queue':{'state':'observed','checked_at':'2026-10-21T19:00:00Z'}}}
        current=project({'tasks':{}},None,observed_at='2026-10-21T19:05:00Z')
        retain_unavailable_queue(current,previous)
        self.assertEqual(current['cards'][0]['title'],'Search')
        self.assertEqual(current['sources']['queue']['state'],'unavailable')
        self.assertEqual(current['sources']['queue']['last_success_at'],'2026-10-21T19:00:00Z')

    def test_pinned_queue_head_cannot_inherit_another_heads_release(self):
        queue={'items':[{'card_id':'q','title':'Search','stage':'fixing',
          'prs':[{'repo':'jbookout/doctorcre-app','number':12,'head':'a'*40}]}]}
        state={'tasks':{'p':{'title':'Search','stage':'live','repo':'jbookout/doctorcre-app','pr':12,'pr_head':'b'*40}}}
        self.assertEqual(project(state,queue)['cards'][0]['state'],'fixing')

    def test_queue_and_portfolio_references_cannot_collapse_the_same_card(self):
        result=project({'tasks':{'search':{'title':'Shared search contract','stage':'build'}}},
                       {'items':[{'card_id':'search','title':'Deal room search','stage':'building'}]})
        self.assertEqual(len({card['id'] for card in result['cards']}),2)

    def test_queue_events_publish_and_read_back_return_merge_release_and_outage(self):
        reads = publication_sequence()
        states = [next(card['state'] for card in [*read['snapshot']['snapshot_json']['orchestrator']['cards'],
                  *read['snapshot']['snapshot_json']['orchestrator']['history']] if card['id']=='queue:search') for read in reads]
        self.assertEqual(states, ['waiting','working','review','fixing','merging','merged','live','live'])
        last=reads[-1]['snapshot']['snapshot_json']['orchestrator']
        self.assertEqual(last['sources']['queue']['state'],'unavailable')
        self.assertTrue(last['sources']['queue']['last_success_at'])


def publication_sequence():
    import copy
    import os
    import sqlite3
    import sys
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    import progress_board as board
    from progress_board_test_support import exercise_fields
    sys.path.insert(0,str(Path(__file__).parent/'room-bridge'))
    from work_queue import WorkQueue
    result=[]
    store={}
    def call(verb,args):
        if verb=='read-progress-board':
            return {'snapshot':copy.deepcopy(store.get(args['board_id'])),'questions':[]}
        if verb=='publish-board-snapshot':
            old=store.get(args['board_id'])
            assert args['base_version']==(old['version'] if old else 0)
            store[args['board_id']]={'board_id':args['board_id'],'version':args['base_version']+1,
                'updated_at':'2026-10-21T19:00:00Z','snapshot_json':json.loads(json.dumps(args['snapshot']))}
            return {'ok':True}
        raise AssertionError(verb)
    with tempfile.TemporaryDirectory() as tmp:
        db=Path(tmp)/'queue.sqlite3'
        sync={'checked_at':'2026-10-21T19:00:00Z','last_verified_at':'2026-10-21T19:00:00Z'}
        state={'project':'carr-v5','tasks':{'source':{'title':'Find a deal across the portfolio','status':'review',
               'stage':'review','repo':'jbookout/doctorcre-app','pr':12,'pr_head':'a'*40}},'github_sync':sync}
        portfolio={'tasks':{'backend':{'title':'Shared search contract','status':'running','stage':'build',
                   'repo':'jbookout/carr-system'}},'github_sync':sync}
        with patch.object(tempfile, 'tempdir', tmp), \
             patch.object(board.repo_lib('machine_role'), 'read_marker', return_value='primary'), \
             patch.dict(os.environ,{'CARR_WORK_QUEUE_DB':str(db),'PROGRESS_BOARD_ROOT':tmp}), \
             patch.object(board,'call_verb',side_effect=call), \
             patch.object(board,'read_json_file',return_value=portfolio), WorkQueue(db) as q:
            now=10
            def publish():
                nonlocal now
                assert q.publish(lambda:board.publish_orchestrator(state),now=now)
                now+=2
                result.append(call('read-progress-board',{'board_id':'orchestrator'}))
            q.enqueue('search',desk='cx-app2',task='PRIVATE BRIEF',actor='test',title='Find a deal across the portfolio',
                      prs=[{'repo':'jbookout/doctorcre-app','number':12,'head':'a'*40}])
            publish()
            claimed=q.claim(eligible_desks={'cx-app2'},actor='runner',owner={});publish()
            q.finish('search',claimed['claim'],{'status':'completed','detail':'PRIVATE RESULT'},actor='worker');publish()
            q.requeue('search',stage='fixing',actor='reviewer',reason='Preserve keyboard focus when results change');publish()
            claimed=q.claim(eligible_desks={'cx-app2'},actor='runner',owner={})
            q.finish('search',claimed['claim'],{'status':'completed'},actor='worker')
            q.transition('search','merging',actor='orchestrator',reason='Independent review passed');publish()
            q.transition('search','done',actor='provider',reason='Provider confirmed the approved head merged')
            state['tasks']['source'].update(stage='merged',pr_phase='Merged',merge_sha='a'*40)
            publish()
            state['tasks']['source'].update(**exercise_fields(source='a'*40,repo='jbookout/doctorcre-app'),stage='live',status='done')
            board.publish_orchestrator(state)
            result.append(call('read-progress-board',{'board_id':'orchestrator'}))
            with patch.object(board.work_queue_module(),'board_projection',side_effect=sqlite3.OperationalError('source offline')):
                board.publish_orchestrator(state)
            result.append(call('read-progress-board',{'board_id':'orchestrator'}))
    assert 'PRIVATE BRIEF' not in json.dumps(result) and 'PRIVATE RESULT' not in json.dumps(result)
    return result


if __name__ == '__main__':
    unittest.main()
