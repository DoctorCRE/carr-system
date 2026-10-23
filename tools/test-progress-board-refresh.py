"""Scheduled source observation survives legacy publication failures."""
import copy
import io
import tempfile
import time
import signal
import json
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import progress_board as board


class ScheduledRefreshTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(board.os.environ, {"PROGRESS_BOARD_ROOT": self.root}))
        self.enterContext(patch.object(board.repo_lib("machine_role"), "read_marker", return_value="primary"))

    def test_legacy_size_failure_still_refreshes_and_publishes_facts(self):
        events = []
        failure = board.SnapshotTooLarge('legacy snapshot too large')
        def publish(project):
            events.append(('publish', project))
            if project == board.LAUNCHD_BOARD:
                raise failure
        with patch.object(board, 'render'), patch.object(board, 'build_all_repos', side_effect=lambda: events.append(('refresh', 'all-repos'))), patch.object(board, 'publish_board', side_effect=publish), patch.object(board, 'poll_board_answers') as poll, patch.object(board, 'publish_needs_joe_local') as local, patch('sys.stderr', new_callable=io.StringIO) as err:
            with self.assertRaises(board.SnapshotTooLarge) as raised:
                board.command_render(Namespace(project=board.LAUNCHD_BOARD, publish=True))
        self.assertIs(raised.exception, failure)
        self.assertEqual(events, [('refresh', 'all-repos'), ('publish', 'carr-v5'), ('publish', 'all-repos')])
        poll.assert_called_once_with(board.LAUNCHD_BOARD)
        local.assert_called_once_with()
        self.assertIn('phase=publication outcome=failed', err.getvalue())

    def test_refreshed_facts_and_outages_reach_orchestrator_before_legacy_error(self):
        for outage in ('none', 'partial', 'full'):
            with self.subTest(outage=outage):
                old = '2026-10-20T12:01:46Z'
                fresh = '2026-10-21T20:00:00Z'
                state = {'project': 'carr-v5', 'tasks': {}, 'questions': {}}
                prior = {'project': 'all-repos', 'tasks': {'app': {'title': 'App delivery', 'repo': 'jbookout/doctorcre-app', 'pr': 332, 'stage': 'review'}}, 'repos': [], 'github_sync': {'checked_at': old, 'last_verified_at': old}}
                merged = {'number': 332, 'title': 'App delivery', 'mergedAt': fresh, 'mergeCommit': {'oid': 'a' * 40}, 'headRefOid': 'b' * 40}
                reads = [([], []), ([], [merged])]
                if outage != 'none':
                    reads[1] = RuntimeError('app GitHub unavailable')
                if outage == 'full':
                    reads[0] = RuntimeError('CARR GitHub unavailable')
                store = {}
                def call(verb, args):
                    key = args['board_id']
                    if verb == 'read-progress-board':
                        return {'snapshot': copy.deepcopy(store.get(key))}
                    self.assertEqual(verb, 'publish-board-snapshot')
                    store[key] = {'version': args['base_version'] + 1, 'snapshot_json': copy.deepcopy(args['snapshot'])}
                    return {'ok': True}
                with tempfile.TemporaryDirectory() as root, patch.object(tempfile, 'tempdir', root), patch.dict(board.os.environ, {'PROGRESS_BOARD_ROOT': root}), patch.object(board, 'render'), patch.object(board, 'CORE_REPOS', ('DoctorCRE/carr-system', 'jbookout/doctorcre-app')), patch.object(board, 'read_repository', side_effect=reads), patch.object(board, 'latest_release') as release, patch.object(board, 'stamp', return_value=fresh), patch.object(board, 'call_verb', side_effect=call), patch.object(board, 'project_board_snapshot', side_effect=board.SnapshotTooLarge('legacy size')), patch.object(board, 'poll_board_answers') as poll, patch.object(board, 'publish_needs_joe_local'), patch('sys.stderr', new_callable=io.StringIO):
                    Path(root, 'boards').mkdir()
                    board.write_json(state)
                    board.write_json(prior)
                    with self.assertRaisesRegex(board.SnapshotTooLarge, 'legacy size'):
                        board.command_render(Namespace(project=board.LAUNCHD_BOARD, publish=True))
                    canonical = board.read_json_file(board.state_path('all-repos'))
                    view = store['orchestrator']['snapshot_json']['orchestrator']
                self.assertEqual(canonical['github_sync']['checked_at'], fresh)
                self.assertEqual(canonical['github_sync']['last_verified_at'], old if outage != 'none' else fresh)
                self.assertEqual(canonical['github_sync']['stale'], outage != 'none')
                self.assertEqual(view['sources']['portfolio'], {'state': 'stale' if outage != 'none' else 'observed', 'checked_at': fresh, 'last_success_at': old if outage != 'none' else fresh})
                self.assertEqual(view['cards'][0]['state'], 'review' if outage != 'none' else 'merged')
                self.assertEqual([call.args[0] for call in release.call_args_list], [] if outage == 'full' else ['DoctorCRE/carr-system'] if outage == 'partial' else ['DoctorCRE/carr-system', 'jbookout/doctorcre-app'])
                poll.assert_called_once_with(board.LAUNCHD_BOARD)

    def test_stalled_answer_stage_is_bounded_and_siblings_still_complete(self):
        def stall(project):
            try:
                time.sleep(0.15)
            except Exception:
                pass
        prior_handler = signal.getsignal(signal.SIGALRM)
        with patch.object(board, "STAGE_TIMEOUT_SECONDS", 0.03, create=True), patch.object(board, "render"), patch.object(board, "build_all_repos"), patch.object(board, "publish_board") as publish, patch.object(board, "poll_board_answers", side_effect=stall), patch.object(board, "publish_needs_joe_local") as local, patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, "answers.*timed_out"):
                board.command_render(Namespace(project=board.LAUNCHD_BOARD, publish=True))
        local.assert_called_once_with()
        self.assertEqual([call.args[0] for call in publish.call_args_list], [board.LAUNCHD_BOARD, board.ALL_REPOS_BOARD])
        self.assertEqual(signal.getsignal(signal.SIGALRM), prior_handler)
        status = json.loads(Path(self.root, "boards", "carr-v5-answers-publication.json").read_text())
        self.assertTrue(status["stale"])
        self.assertIsNone(status["last_verified_at"])
        self.assertEqual(status["outcome"], "timed_out")

    def test_failed_source_refresh_cannot_suppress_answer_and_list_refresh(self):
        failure = RuntimeError("synthetic source outage")
        with patch.object(board, "render", side_effect=failure), patch.object(board, "build_all_repos"), patch.object(board, "publish_board"), patch.object(board, "poll_board_answers") as poll, patch.object(board, "publish_needs_joe_local") as local, patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(RuntimeError) as raised:
                board.command_render(Namespace(project=board.LAUNCHD_BOARD, publish=True))
        self.assertIs(raised.exception, failure)
        poll.assert_called_once_with(board.LAUNCHD_BOARD)
        local.assert_called_once_with()

    def test_success_then_failure_keeps_last_verified_stage_timestamp(self):
        common = [patch.object(board, "render"), patch.object(board, "build_all_repos"), patch.object(board, "publish_board"), patch.object(board, "publish_needs_joe_local"), patch("sys.stderr", new_callable=io.StringIO)]
        for item in common:
            self.enterContext(item)
        with patch.object(board, "poll_board_answers"):
            board.command_render(Namespace(project=board.LAUNCHD_BOARD, publish=True))
        path = Path(self.root, "boards", "carr-v5-answers-publication.json")
        success = json.loads(path.read_text())
        with patch.object(board, "poll_board_answers", side_effect=RuntimeError("synthetic outage")):
            with self.assertRaisesRegex(RuntimeError, "synthetic outage"):
                board.command_render(Namespace(project=board.LAUNCHD_BOARD, publish=True))
        failed = json.loads(path.read_text())
        self.assertFalse(success["stale"])
        self.assertTrue(failed["stale"])
        self.assertEqual(failed["last_verified_at"], success["last_verified_at"])

    def test_diagnostic_write_failure_does_not_prevent_product_work(self):
        completed = []
        failure = OSError("synthetic read-only status directory")
        with patch.object(board, "record_publication_stage", side_effect=failure), patch("sys.stderr", new_callable=io.StringIO):
            error = board.run_publication_stage("synthetic", "answers", lambda: completed.append("answered"))
        self.assertEqual(completed, ["answered"])
        self.assertIs(error, failure)


if __name__ == '__main__':
    unittest.main()
