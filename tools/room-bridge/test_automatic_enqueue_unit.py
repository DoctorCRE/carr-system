"""Automatic queue admission must use the guarded Codex desks."""

import unittest

from kanban_adapter import QueueService, load_catalog


class RecordingAdapter:
    def __init__(self):
        self.created = []

    def create(self, command, incoming, target):
        self.created.append(command)
        return {"created": True, "task_id": "t_fixture0001"}


class AutomaticEnqueueTests(unittest.TestCase):
    def enqueue(self, alias, desk, *, automatic_route=False, human=False):
        catalog = {"v": 1, "targets": {alias: {
            "enabled": True, "adapter": "desk", "desk": desk,
            "assignee": f"desk:{desk}", "capabilities": ["repo-write"],
        }}}

        class Router:
            def load_policy(self):
                return {"queue_targets": {"direct": "missing", "fallback": alias}}

            def decide(self, *args, **kwargs):
                return {"route": "direct"}

        adapter = RecordingAdapter()
        service = QueueService(catalog=catalog, adapter=adapter, router=Router(), flash_up=lambda: False)
        target = "auto" if automatic_route else alias
        result = service.handle({
            "body": f"@queue enqueue target={target} cap=repo-write :: Fix the release",
            "msg_id": "fixture-message", "seq": 1,
            "origin_channel": "browser-human" if human else "mcp",
            "origin_actor": "joe" if human else "codex",
        }, room="model-room")
        return result, adapter.created

    def test_model_enqueue_and_auto_fallback_refuse_max_desks_including_aliases(self):
        for desk in ("claude-desktop", "joe-desk", "claude-session"):
            for alias in (desk, "innocent-alias"):
                for automatic_route, human in ((False, False), (True, False), (True, True)):
                    with self.subTest(desk=desk, alias=alias, auto=automatic_route, human=human):
                        result, created = self.enqueue(alias, desk, automatic_route=automatic_route, human=human)
                        self.assertEqual(result["kind"], "rejected")
                        self.assertEqual(result["receipt"]["queue_rejected"]["code"], "unsafe_automatic_desk")
                        self.assertEqual(created, [])

    def test_guarded_codex_desks_accept_automatic_work(self):
        for desk in ("luna-desk", "sol-fixer", "codex-desk"):
            with self.subTest(desk=desk):
                result, created = self.enqueue("fixture", desk)
                self.assertEqual(result["kind"], "accepted")
                self.assertEqual(len(created), 1)

    def test_explicit_browser_human_enqueue_keeps_its_named_desk(self):
        result, created = self.enqueue("claude", "joe-desk", human=True)
        self.assertEqual(result["kind"], "accepted")
        self.assertEqual(len(created), 1)

    def test_release_fixer_catalog_accepts_repo_write_on_sol_fixer(self):
        entry = load_catalog()["targets"]["sol-fixer"]
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["adapter"], "desk")
        self.assertEqual(entry["desk"], "sol-fixer")
        self.assertIn("repo-write", entry["capabilities"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
