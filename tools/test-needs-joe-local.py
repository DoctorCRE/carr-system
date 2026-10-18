#!/usr/bin/env python3
"""The local half of the needs-Joe list: out/orch/needs-joe-or-wait.txt plus
live PR state, re-derived from scratch on every board run so an item leaves the
moment its PR closes or its line is removed."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("needs_joe_local", ROOT / "tools/needs_joe_local.py")
assert spec is not None and spec.loader is not None
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)

FILE = """\
2026-10-02T22:55:43 jbookout/doctorcre-app#131 WAITING ON jbookout/doctorcre-app#129
2026-10-02T23:08:59 jbookout/carr-system#1244 NEEDS JOE: Restore TypeSafe API credits so the required checks can complete
2026-10-02T23:22:49 jbookout/carr-system#1477 NEEDS JOE: Replenish the credits
2026-10-02T23:29:24 jbookout/carr-system#1431 NEEDS JOE: Restore TypeSafe API credits for pre-push
2026-10-03T15:48:26 TABLED (Joe away): Codex and xAI sign-ins on the Studio
2026-10-03T16:05:18 TABLED: Design Manager slice 3 needs a Codex web environment
2026-10-04T04:32:20 jbookout/doctorcre-app#146 NEEDS JOE: Approve the new color tokens
2026-10-04T05:32:20 jbookout/doctorcre-app#146 SUPERSEDED
not a line the orchestrator writes

"""

PRS = {
    ("jbookout/doctorcre-app", 131): {"state": "OPEN", "title": "Board lane"},
    ("jbookout/carr-system", 1244): {"state": "OPEN", "title": "Route typed calls through TypeSafe"},
    ("jbookout/carr-system", 1477): {"state": "MERGED", "title": "Jev selftest"},
    ("jbookout/doctorcre-app", 146): {"state": "OPEN", "title": "Color tokens"},
}


def lookup(repo, number):
    if (repo, number) not in PRS:
        raise RuntimeError("gh unreachable")
    return PRS[(repo, number)]


class NeedsJoeLocalTests(unittest.TestCase):
    def setUp(self):
        self.items = local.derive_items(FILE, lookup)
        self.by_pr = {(item["repo"], item["number"]): item for item in self.items if item["repo"]}

    def test_open_needs_joe_pr_is_listed_with_its_title(self):
        item = self.by_pr[("jbookout/carr-system", 1244)]
        self.assertEqual(item["kind"], "needs_joe")
        self.assertEqual(item["pr_state"], "OPEN")
        self.assertEqual(item["pr_title"], "Route typed calls through TypeSafe")
        self.assertEqual(item["text"], "Restore TypeSafe API credits so the required checks can complete")
        self.assertEqual(item["at"], "2026-10-02T23:08:59")

    def test_closed_pr_clears_its_item(self):
        self.assertNotIn(("jbookout/carr-system", 1477), self.by_pr)

    def test_a_later_superseded_line_clears_the_pr(self):
        self.assertNotIn(("jbookout/doctorcre-app", 146), self.by_pr)

    def test_unreadable_pr_state_keeps_the_item_marked_unknown(self):
        item = self.by_pr[("jbookout/carr-system", 1431)]
        self.assertEqual(item["pr_state"], "UNKNOWN")

    def test_waiting_lines_pass_through_for_the_verb_to_exclude(self):
        self.assertEqual(self.by_pr[("jbookout/doctorcre-app", 131)]["kind"], "waiting")

    def test_tabled_lines_stay_until_removed(self):
        tabled = [item["text"] for item in self.items if item["kind"] == "tabled"]
        self.assertEqual(tabled, ["Codex and xAI sign-ins on the Studio",
                                  "Design Manager slice 3 needs a Codex web environment"])

    def test_removing_the_line_removes_the_item(self):
        items = local.derive_items(FILE.replace("2026-10-03T15:48:26 TABLED (Joe away): Codex and xAI sign-ins on the Studio\n", ""), lookup)
        self.assertNotIn("Codex and xAI sign-ins on the Studio", [item["text"] for item in items])

    def test_page_is_bounded_and_versioned(self):
        page = local.local_page(self.items, "2026-10-05T12:00:00Z")
        self.assertEqual(page["schema"], "needs-joe-local.v1")
        self.assertEqual(page["observed_at"], "2026-10-05T12:00:00Z")
        self.assertEqual(page["items"], self.items)
        long = local.derive_items("2026-10-05T00:00:00 TABLED: " + "x" * 900 + "\n", lookup)
        self.assertLessEqual(len(long[0]["text"]), 300)

    def test_missing_file_is_an_empty_source(self):
        self.assertEqual(local.read_source(ROOT / "out/does-not-exist.txt"), "")


class PublishTests(unittest.TestCase):
    def run_publish(self, remote):
        calls = []

        def call_verb(verb, args):
            calls.append((verb, args))
            if verb == "read-progress-board":
                published = [a for v, a in calls if v == "publish-board-snapshot"]
                snapshot = ({"version": 3, "snapshot_json": published[-1]["snapshot"]} if published else remote)
                return {"ok": True, "snapshot": snapshot, "questions": []}
            if verb == "publish-board-snapshot":
                return {"ok": True}
            raise AssertionError(verb)
        count = local.publish(call_verb, lambda verb, args: "k", lookup, FILE, "2026-10-05T12:00:00Z")
        return count, calls

    def test_publishes_the_page_and_verifies_the_read_back(self):
        count, calls = self.run_publish(remote=None)
        self.assertEqual([verb for verb, _ in calls],
                         ["read-progress-board", "publish-board-snapshot", "read-progress-board"])
        publish = calls[1][1]
        self.assertEqual(publish["board_id"], "needs-joe-local")
        self.assertEqual(publish["base_version"], 0)
        self.assertEqual(count, len(local.derive_items(FILE, lookup)))

    def test_every_run_republishes_on_the_remote_version_so_freshness_is_real(self):
        items = local.derive_items(FILE, lookup)
        remote = {"version": 2, "snapshot_json": local.local_page(items, "2026-10-05T11:45:00Z")}
        _, calls = self.run_publish(remote=remote)
        publish = [args for verb, args in calls if verb == "publish-board-snapshot"]
        self.assertEqual(len(publish), 1)
        self.assertEqual(publish[0]["base_version"], 2)
        self.assertEqual(publish[0]["snapshot"]["observed_at"], "2026-10-05T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
