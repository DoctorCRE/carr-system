#!/usr/bin/env python3
"""Offline tests for the Jev call-site registry, budgets and quiet cap notices.

NOTHING HERE REACHES THE NETWORK OR SENDS MAIL. Paid transports are an
injected urlopen, the cap counter lives in a temporary directory, and the
alarm sinks run through the CARR_JEV_ALERT_SINK dry-run seam.

What this suite holds (2026-10-04 system-wide Jev audit):
  * a paid call needs a registered site, that site's attribution, and room in
    its own and the global hourly budget; every refusal happens before any
    transport, is logged once, and never counts as a paid attempt;
  * the hourly cap is real and resets on the hour;
  * a reached cap surfaces ONE line per session per cap window, with the reset;
  * the alarm mail names its failure (mail_unconfigured) instead of a bare
    CalledProcessError, and tests never send mail;
  * every source file that can make a paid call is in the registry, and every
    registry entry names files that exist (the CI guard against a new burner).
"""

from __future__ import annotations

import ast
import importlib.util
import io
import multiprocessing
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env
ENV = fixture_env()

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("typesafe_client_sites", REPO / "ops" / "typesafe_client.py")
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
REGISTRY_PATH = REPO / "ops" / "config" / "jev-call-sites.v1.json"


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ANSWER = {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}},
          "usage": {"input_tokens": 10, "output_tokens": 2}}


def site(caller, **overrides):
    entry = {"caller": caller, "trigger": "fixture", "runs_in": "fixture",
             "attribution": "session", "unattended": "off", "hourly_budget": 50,
             "daily_budget": 500, "owner": "orchestrator", "value": "fixture",
             "sources": ["ops/typesafe_client.py"]}
    entry.update(overrides)
    return entry


class Harness(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = root
        self.enterContext(patch.dict(os.environ, CARR_JEV_SEMANTIC_CACHE=str(root / "semantic-cache")))
        self.log = root / "calls.jsonl"
        self.registry = root / "sites.json"
        self.write_registry([site("hook_site"), site("job_site", attribution="session_or_job"),
                             site("brief:*", unattended="allowed")], hourly=100)
        self.enterContext(patch.object(client, "JEV_DAILY_CAP_LOG", str(self.log)))
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(self.registry)))
        self.enterContext(patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1000))
        self.requests = []

        def opener(request, timeout=None):
            self.requests.append(request)
            return FakeResponse(json.dumps(ANSWER).encode())
        self.enterContext(patch.object(client.urllib.request, "urlopen", opener))
        self.enterContext(patch.object(client, "_launch_spend_alert_worker", lambda *a: None))
        # Hermetic credentials: a fixture admission secret, and no local vendor
        # key unless a test supplies one, exactly as on a hosted runner.
        self.enterContext(patch.object(client, "read_admission_secret", lambda: "offline-admission"))
        self.enterContext(patch.object(client, "read_api_key", side_effect=client.TypeSafeError(
            "fixture holds no local vendor key")))
        env = {k: v for k, v in os.environ.items()
               if k not in client.SESSION_ID_ENV_KEYS + ("CARR_JEV_JOB", "XPC_SERVICE_NAME", "CARR_JEV_WORKER",
                                                         "CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE")}
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.clock = self.enterContext(patch.object(client, "datetime", wraps=datetime))
        self.now(2026, 10, 4, 3, 10)

    def now(self, *parts):
        self.clock.now.return_value = datetime(*parts, tzinfo=timezone.utc)

    def write_registry(self, sites, hourly=100):
        self.registry.write_text(json.dumps({"schema": "carr-jev-call-sites/v1",
                                             "hourly_paid_call_cap": hourly, "sites": sites}))

    def ask(self, text, caller="hook_site", session="sess-1"):
        return client.ask(text, {"q": client.noul("Fixture judgment")}, api_key="offline-fixture",
                          calls_log=str(self.log), cache_ttl_seconds=0, caller=caller,
                          session_id=session)

    def rows(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]


class WildcardBudgetTests(Harness):
    def test_suffixes_share_hourly_daily_budget_pause_and_health(self):
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(REGISTRY_PATH)))
        for hour in range(4):
            self.now(2026, 10, 4, hour, 10)
            for suffix in range(10):
                self.ask(f"{hour}-{suffix}", caller=f"adhoc:probe{hour}-{suffix}")
            with self.assertRaises(client.JevCallRefused):
                self.ask("extra", caller=f"adhoc:extra{hour}")
            self.assertIsNotNone(client.active_pause(sites=["adhoc:another"]))
        self.now(2026, 10, 4, 4, 10)
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("new hour", caller="adhoc:new")
        self.assertEqual(caught.exception.code, "site_daily_budget")
        self.assertEqual(len(self.requests), 40)
        health = client.spend_by_site_health()
        self.assertIn("WARN", health)
        self.assertIn("adhoc:*=40/40", health)
        self.assertNotIn("/?", health)
        self.assertTrue(any(row.get("caller") == "adhoc:probe0-0" for row in self.rows()))


class BudgetUpgradeTests(Harness):
    def seed_receipts(self, count, caller="adhoc:legacy"):
        self.log.write_text("".join(json.dumps({"ts": "2026-10-04T03:01:00Z",
                                               "caller": caller, "cache_hit": False}) + "\n"
                                    for _ in range(count)))

    def test_existing_daily_counter_upgrade_preserves_hour_and_wildcard_usage(self):
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(REGISTRY_PATH)))
        self.seed_receipts(200)
        with client.sqlite3.connect(client._cap_db_path()) as db:
            db.execute("CREATE TABLE daily_cap (day TEXT PRIMARY KEY, attempts INTEGER, notified INTEGER)")
            db.execute("INSERT INTO daily_cap VALUES ('2026-10-04',200,0)")
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("attempt 201", caller="adhoc:new")
        self.assertEqual(caught.exception.code, "hourly_paid_call_cap")
        self.assertEqual(self.requests, [])
        health = client.spend_by_site_health()
        self.assertIn("200/1000 paid attempts", health)
        self.assertIn("this hour 200/200", health)
        self.assertIn("adhoc:*=200/40", health)

    def test_receipt_rebuild_seeds_once_and_excludes_free_or_refused_calls(self):
        self.write_registry([site("hook_site", hourly_budget=3, daily_budget=3)])
        self.seed_receipts(2, "hook_site")
        with self.log.open("a") as fh:
            for extra in ({"cache_hit": True}, {"error": "unattributed_call"}):
                fh.write(json.dumps({"ts": "2026-10-04T03:01:00Z", "caller": "hook_site",
                                     "cache_hit": False, **extra}) + "\n")
        self.ask("third")
        for _ in range(2):
            with self.assertRaises(client.JevCallRefused):
                self.ask("fourth")
        self.assertEqual(len(self.requests), 1)
        health = client.spend_by_site_health()
        self.assertIn("3/1000 paid attempts", health)
        self.assertIn("this hour 3/100", health)
        self.assertIn("hook_site=3/3", health)

    def test_legacy_counter_without_receipts_is_conservatively_attributed(self):
        self.write_registry([site("hook_site", hourly_budget=3, daily_budget=3)])
        with client.sqlite3.connect(client._cap_db_path()) as db:
            db.execute("CREATE TABLE daily_cap (day TEXT PRIMARY KEY, attempts INTEGER, notified INTEGER)")
            db.execute("INSERT INTO daily_cap VALUES ('2026-10-04',3,0)")
        with self.assertRaises(client.JevCallRefused):
            self.ask("unknown past attempts still consume site budget")
        self.assertEqual(self.requests, [])
        health = client.spend_by_site_health()
        self.assertIn("this hour 3/100", health)
        self.assertIn("hook_site=3/3", health)
        self.assertIn("unattributed=3", health)


class RecordingAttributionTests(Harness):
    def test_keyless_controller_execution_does_not_invent_job_attribution(self):
        spec = importlib.util.spec_from_file_location("keyless_controller", REPO / "tools/control-plane.py")
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        workflow = {"execution": {"entrypoint": "bin/nightly.sh", "args": [], "shadow_args": []}}
        with patch.dict(os.environ, {"CARR_JEV_JOB": "unrelated-parent"}), \
                patch.object(controller.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            controller._execute_deterministic(workflow, {}, timeout=10, mode="shadow")
        env = run.call_args.kwargs["env"]
        self.assertNotIn("CARR_JEV_JOB", env)
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(client._job_label())

    def test_controller_nightly_child_can_read_deals_without_agent_environment(self):
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(REGISTRY_PATH)))
        spec = importlib.util.spec_from_file_location("nightly_controller", REPO / "tools/control-plane.py")
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        manifest = json.loads(controller.MANIFEST_PATH.read_text())
        workflow = next(w for w in manifest["workflows"] if w["key"] == "nightly-record-layer")
        with patch.object(controller.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            controller._execute_deterministic(workflow, {"scheduled_for": "2026-10-04T03:00:00Z"},
                                              timeout=10, mode="live")
        env = {**run.call_args.kwargs["env"], "CARR_JEV_SEMANTIC_CACHE": str(self.log)+".semantic"}
        self.assertFalse(any(key in env for key in client.SESSION_ID_ENV_KEYS))
        with patch.dict(os.environ, env, clear=True):
            _, entry = client._admit_paid_call("jev_deal_read", None, {}, [], None, None)
            self.assertEqual(entry["caller"], "jev_deal_read")
            self.assertEqual(client._job_label(), "nightly-record-layer")
            spec = importlib.util.spec_from_file_location("jev_deal_read", REPO / "ops/jev_deal_read.py")
            reader = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reader)
            bundle = {"has_evidence": True, "evidence_chars": 900, "name": "Fixture deal",
                      "client": "Fixture practice", "phase": "research", "deal_type": "startup",
                      "segment": "Dental", "city": "Fixture city", "owner": "fixture",
                      "next_step": "Review terms", "next_step_due": None,
                      "status_narrative": "Terms discussed", "history": [], "days_since_record_touched": 1}
            answers = {"movement": {"type": "score", "score": 1.0, "confidence": 0.9},
                       "waiting_on": {"type": "choice", "choice": "client", "confidence": 0.9},
                       "silence_is_bad": {"type": "noul", "noul": 0.1}}
            def opener(request, timeout=None):
                self.requests.append(request)
                return FakeResponse(json.dumps({**ANSWER, "answers": answers}).encode())
            with patch.object(reader, "ts", client), patch.object(client.urllib.request, "urlopen", opener), \
                    patch.dict(client.ask.__kwdefaults__, cache_path=str(self.root / "deal-cache.json"),
                               calls_log=str(self.log)):
                reading = reader.read_deal(bundle, api_key="offline-fixture")
            self.assertTrue(reading["judged"], reading.get("reason"))
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(self.rows()[-1]["job"], "nightly-record-layer")

    def test_quill_post_call_checks_work_without_agent_environment(self):
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(REGISTRY_PATH)))
        self.enterContext(patch.dict(os.environ, {"XPC_SERVICE_NAME": "com.digimata.quill"}))
        self.enterContext(patch.object(client, "read_api_key", lambda *a: "offline-fixture"))
        spec = importlib.util.spec_from_file_location("post_call_jev", REPO / "tools/dictation-rig/bin/post_call_jev.py")
        post = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(post)
        self.enterContext(patch.object(post, "_client", lambda: client))
        answers = {"deal_match": {"type": "choice", "choice": "deal-fixture", "confidence": 0.99},
                   "speaker_right": {"type": "noul", "noul": 0.99},
                   "details_supported": {"type": "noul", "noul": 0.99}}
        def opener(request, timeout=None):
            self.requests.append(request)
            asked = json.loads(request.data)["questions"]
            batched = {key: answers[key.split(":")[-1]] for key in asked}
            return FakeResponse(json.dumps({**ANSWER, "answers": batched}).encode())
        self.enterContext(patch.object(client.urllib.request, "urlopen", opener))
        self.enterContext(patch.dict(client.ask.__kwdefaults__, cache_path=str(self.root / "cache.json"),
                                     calls_log=str(self.log)))
        pack = {"session": "recording-fixture", "joe_tasks": [
            {"id": "item-fixture", "deal_id": "deal-fixture", "task": "send details", "evidence": "send details"}]}
        post.check_distillation(pack, {"deals": [{"id": "deal-fixture", "name": "Fixture"}]},
                                {"segments": [{"speaker": "Me", "text": "send details"}]})
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("unavailable", pack["joe_tasks"][0]["checks"])
        self.assertTrue(self.rows(), "recording receipts stay in the isolated fixture log")
        self.assertEqual(client._job_label(), "com.digimata.quill")
        with patch.dict(os.environ, {"XPC_SERVICE_NAME": "com.unrelated.service"}):
            self.assertIsNone(client._job_label())


class RegistryAdmissionTests(Harness):
    def test_unregistered_caller_is_refused_before_transport_and_logged_once(self):
        for _ in range(3):
            with self.assertRaises(client.JevCallRefused) as caught:
                self.ask("x", caller="brand_new_burner")
            self.assertEqual(caught.exception.code, "unregistered_caller")
        self.assertEqual(self.requests, [])
        refusals = [r for r in self.rows() if r.get("error") == "unregistered_caller"]
        self.assertEqual(len(refusals), 1, "a refusal is visible once per window, not per call")
        self.assertFalse(refusals[0]["ok"])

    def test_refusals_never_count_toward_the_daily_cap_seed(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="brand_new_burner")
        day = "2026-10-04"
        self.assertEqual(client._logged_attempts(str(self.log), day), 0)

    def test_session_site_without_session_is_refused(self):
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("x", session=None)
        self.assertEqual(caught.exception.code, "unattributed_call")
        self.assertEqual(self.requests, [])
        self.ask("x", session="sess-1")
        self.assertEqual(len(self.requests), 1)

    def test_job_attribution_comes_from_carr_job_or_launchd_label(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="job_site", session=None)
        with patch.dict(os.environ, XPC_SERVICE_NAME="application.com.apple.Terminal"):
            with self.assertRaises(client.JevCallRefused):
                self.ask("y", caller="job_site", session=None)
        with patch.dict(os.environ, XPC_SERVICE_NAME="com.carr.deal-read"):
            self.ask("z", caller="job_site", session=None)
        with patch.dict(os.environ, CARR_JEV_JOB="release-pipeline"):
            self.ask("w", caller="job_site", session=None)
        self.assertEqual(len(self.requests), 2)
        jobs = [r.get("job") for r in self.rows() if r.get("ok")]
        self.assertEqual(jobs, ["com.carr.deal-read", "release-pipeline"])

    def test_unattended_worker_runs_only_sites_that_allow_it(self):
        with patch.dict(os.environ, CARR_JEV_WORKER="off"):
            with self.assertRaises(client.JevCallRefused) as caught:
                self.ask("x")
            self.assertEqual(caught.exception.code, "unattended_worker_off")
            self.ask("explicit brief judgment", caller="brief:w3_builder")
        self.assertEqual(len(self.requests), 1)

    def test_fixture_and_ci_runs_never_pay_and_never_log(self):
        for flag in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE"):
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: "1"}):
                with self.assertRaises(client.JevCallRefused) as caught:
                    self.ask("x")
                self.assertEqual(caught.exception.code, "fixture_offline")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.rows(), [], "fixture traffic stays out of the real call log")

    def test_prefix_entry_needs_a_suffix(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="brief:")

    def test_unreadable_registry_admits_nothing(self):
        self.registry.write_text("{")
        with self.assertRaises(client.TypeSafeError):
            self.ask("x")
        self.assertEqual(self.requests, [])

    def test_offline_injected_opener_is_not_policed(self):
        result = client.ask("x", {"q": client.noul("Fixture")}, api_key="k", caller="anything",
                            opener=lambda request, timeout=None: FakeResponse(json.dumps(ANSWER).encode()),
                            calls_log=str(self.log), cache_ttl_seconds=0)
        self.assertEqual(result["model"], "jev-1.13.0")


class BudgetTests(Harness):
    def test_global_hourly_cap_holds_and_resets_on_the_hour(self):
        self.write_registry([site("hook_site", hourly_budget=50, daily_budget=500)], hourly=3)
        for i in range(3):
            self.ask(f"a{i}")
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("over")
        self.assertEqual(caught.exception.code, "hourly_paid_call_cap")
        self.assertEqual(caught.exception.resets_at, "2026-10-04T04:00:00Z")
        self.assertEqual(len(self.requests), 3)
        self.now(2026, 10, 4, 4, 0)
        self.ask("next hour")
        self.assertEqual(len(self.requests), 4)

    def test_site_hourly_and_daily_budgets_hold(self):
        self.write_registry([site("hook_site", hourly_budget=2, daily_budget=3),
                             site("job_site", attribution="session_or_job")], hourly=100)
        self.ask("1")
        self.ask("2")
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("3")
        self.assertEqual(caught.exception.code, "site_hourly_budget")
        self.ask("other site keeps working", caller="job_site")
        self.now(2026, 10, 4, 5, 0)
        self.ask("3")
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("4")
        self.assertEqual(caught.exception.code, "site_daily_budget")
        self.assertEqual(caught.exception.resets_at, "2026-10-05T00:00:00Z")
        self.assertEqual(len(self.requests), 4)

    def test_budget_refusal_is_logged_once_per_window(self):
        self.write_registry([site("hook_site", hourly_budget=1, daily_budget=10)], hourly=100)
        self.ask("1")
        for _ in range(4):
            with self.assertRaises(client.JevCallRefused):
                self.ask("2")
        self.assertEqual(len([r for r in self.rows() if r.get("error") == "site_hourly_budget"]), 1)

    def test_daily_cap_still_applies_above_site_budgets(self):
        with patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1):
            self.ask("1")
            with self.assertRaisesRegex(client.TypeSafeError, "daily paid call cap"):
                self.ask("2")

    def test_shared_cap_holds_across_overlapping_site_limits(self):
        self.write_registry([site("hook_site", hourly_budget=3, daily_budget=3),
                             site("job_site", hourly_budget=3, daily_budget=3)], hourly=100)
        with patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=2):
            self.ask("first site")
            self.ask("second site", caller="job_site")
            for caller in ("hook_site", "job_site"):
                with self.assertRaises(client.JevCallRefused) as caught:
                    self.ask("exhausted", caller=caller)
                self.assertEqual(caught.exception.code, "daily_paid_call_cap")
        self.assertEqual(len(self.requests), 2)


class QuietNoticeTests(Harness):
    def test_cap_reached_surfaces_one_line_per_session_per_window(self):
        self.write_registry([site("hook_site")], hourly=1)
        self.ask("1")
        with self.assertRaises(client.JevCallRefused):
            self.ask("2")
        pause = client.active_pause()
        self.assertEqual(pause["scope"], "hourly_paid_call_cap")
        first = client.pause_notice("sess-A")
        self.assertIn("04:00", first)
        self.assertIn("paused", first.lower())
        self.assertIsNone(client.pause_notice("sess-A"))
        self.assertIsNotNone(client.pause_notice("sess-B"), "each session hears it once")
        self.now(2026, 10, 4, 4, 1)
        self.assertIsNone(client.active_pause())
        self.assertIsNone(client.pause_notice("sess-A"))

    def test_daily_cap_pause_names_utc_midnight(self):
        with patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1):
            self.ask("1")
            with self.assertRaises(client.TypeSafeError):
                self.ask("2")
            pause = client.active_pause()
        self.assertEqual(pause, {"scope": "daily_paid_call_cap", "resets_at": "2026-10-05T00:00:00Z"})

    def test_site_budget_pause_is_scoped_to_the_named_sites(self):
        self.write_registry([site("hook_site", hourly_budget=1, daily_budget=10),
                             site("job_site", attribution="session_or_job")], hourly=100)
        self.ask("1")
        with self.assertRaises(client.JevCallRefused):
            self.ask("2")
        self.assertIsNone(client.active_pause())
        self.assertEqual(client.active_pause(sites=["hook_site"])["scope"], "site_hourly_budget")
        self.assertIsNone(client.active_pause(sites=["job_site"]))


class SpendHealthTests(Harness):
    def test_health_line_reports_spend_by_site_with_bound_action(self):
        self.write_registry([site("hook_site", daily_budget=2, hourly_budget=2),
                             site("job_site", attribution="session_or_job")], hourly=100)
        self.ask("1")
        self.ask("2")
        self.ask("3", caller="job_site")
        line = client.spend_by_site_health()
        self.assertTrue(line.startswith("WARN jev spend by site"), line)
        self.assertIn("hook_site=2/2", line)
        self.assertIn("job_site=1/500", line)
        self.assertIn("owner orchestrator", line)
        self.assertIn("ops/config/jev-call-sites.v1.json", line)

    def test_canonical_health_prints_the_site_spend_row(self):
        source = (REPO / "tools" / "health-check.py").read_text(encoding="utf-8")
        self.assertIn("client.spend_by_site_health()", source)
        self.assertIn('_red("jev_site_budget"', source)

    def test_health_line_is_ok_when_every_site_is_inside_budget(self):
        self.ask("1")
        self.assertTrue(client.spend_by_site_health().startswith("OK jev spend by site"))


class AlarmMailTests(unittest.TestCase):
    def test_missing_mail_credential_is_named_not_called_process_error(self):
        with patch.object(client, "GMAIL_ENV_PATH", "/nonexistent/gmail.env"), \
                patch.dict(os.environ, {"CARR_GMAIL_USER": "", "CARR_GMAIL_APP_PASSWORD": "",
                                        "CARR_JEV_ALERT_SINK": ""}), \
                patch.object(client.subprocess, "run") as run:
            with self.assertRaises(client.AlarmSinkError) as caught:
                client._email_spend_alert({"message": "m", "threshold": 50})
        self.assertEqual(str(caught.exception), "mail_unconfigured")
        run.assert_not_called()

    def test_failed_handover_reports_a_category_from_its_stderr(self):
        failure = subprocess.CalledProcessError(1, ["gmail-handover"], stderr="login refused: (535)")
        with tempfile.NamedTemporaryFile() as env_file, \
                patch.object(client, "GMAIL_ENV_PATH", env_file.name), \
                patch.dict(os.environ, {"CARR_JEV_ALERT_SINK": ""}), \
                patch.object(client.subprocess, "run", side_effect=failure):
            with self.assertRaises(client.AlarmSinkError) as caught:
                client._email_spend_alert({"message": "m", "threshold": 50})
        self.assertEqual(str(caught.exception), "mail_auth_refused")

    def test_dry_run_seam_records_both_sinks_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            sink = Path(root) / "alerts.jsonl"
            with patch.dict(os.environ, CARR_JEV_ALERT_SINK=f"dry-run:{sink}"), \
                    patch.object(client.subprocess, "run") as run:
                client._emit_spend_alert({"message": "m", "threshold": 80})
                client._email_spend_alert({"message": "m", "threshold": 80})
            run.assert_not_called()
            rows = [json.loads(line) for line in sink.read_text().splitlines()]
        self.assertEqual([r["sink"] for r in rows], ["notification", "mail"])




class ChangeTollsPredicateTests(unittest.TestCase):
    def test_repeated_changed_paths_are_pure_and_do_not_transport(self):
        spec = importlib.util.spec_from_file_location('tolls_under_test', REPO/'ops/jev_change_tolls.py')
        tolls = importlib.util.module_from_spec(spec); spec.loader.exec_module(tolls)
        class Explosive:
            def __getattr__(self, name): raise AssertionError(name)
        state = {'files':['ops/ci.sh']}
        first = tolls.owed(state, client=Explosive())
        self.assertEqual(first,tolls.owed(state,client=Explosive()))
        self.assertEqual([n for _,n,_ in first],['ci_sh_reseal','inventory_reseal'])

    def test_collector_reads_changed_content_without_model(self):
        spec = importlib.util.spec_from_file_location('tolls_collector', REPO/'ops/jev_change_tolls.py')
        tolls = importlib.util.module_from_spec(spec); spec.loader.exec_module(tolls)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['git','init','-q',tmp],env=ENV,check=True)
            subprocess.run(['git','remote','add','origin',tmp],cwd=tmp,env=ENV,check=True)
            (root/'fixture.py').write_text('old')
            subprocess.run(['git','add','fixture.py'],cwd=tmp,env=ENV,check=True)
            subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],cwd=tmp,env=ENV,check=True)
            (root/'fixture.py').write_text('first')
            first=tolls.change(base='HEAD',repo=tmp)
            (root/'fixture.py').write_text('second')
            second=tolls.change(base='HEAD',repo=tmp)
            self.assertNotEqual(first['file_content_sha256'],second['file_content_sha256'])


class WorkerBreakerTests(Harness):
    def worker(self, failures):
        calls = []

        def runner(argv, **kwargs):
            mode = json.loads(argv[3]).get("transport_mode")
            calls.append(mode)
            if mode == "cache_only":
                return subprocess.CompletedProcess(argv, 1, "", '{"error":"jev_cache_miss"}')
            if failures:
                return subprocess.CompletedProcess(argv, 1, "", '{"error":"jev_upstream_failed"}')
            return subprocess.CompletedProcess(argv, 0, json.dumps({**ANSWER, "ok": True,
                                                                    "receipt_id": "w"}), "")
        return calls, runner

    def ask_worker(self, text, runner):
        with patch.object(client, "read_api_key", return_value="offline-fixture"), \
                patch.dict(os.environ, CARR_JEV_IN_HOOK="0"):
            return client.ask(text, {"q": client.noul("Fixture")}, calls_log=str(self.log),
                              cache_ttl_seconds=0, caller="hook_site", session_id="sess-1",
                              server_runner=runner)

    def test_a_failing_worker_is_paid_once_then_skipped_for_the_breaker_window(self):
        calls, runner = self.worker(failures=True)
        self.ask_worker("one", runner)
        self.assertEqual(calls.count("paid_once"), 1)
        self.ask_worker("two", runner)
        self.ask_worker("three", runner)
        self.assertEqual(calls.count("paid_once"), 1, "breaker open: no doomed Worker attempts")
        day = "2026-10-04"
        db = client.sqlite3.connect(str(self.log) + ".daily-cap.sqlite3")
        attempts = db.execute("SELECT attempts FROM daily_cap WHERE day=?", (day,)).fetchone()[0]
        db.close()
        self.assertEqual(attempts, 4, "one doomed Worker try, then one reservation per call")


class TransportAdmissionTests(Harness):
    def test_concurrent_worktrees_reserve_the_last_shared_slot_once(self):
        repository = self.root / "repo"
        repository.mkdir()
        paths = ["ops/typesafe_client.py", "ops/config/jev-cost-guard.v1.json",
                 "tools/judge/interface.py", "mcp-server/src/jev-request-contract.v1.json",
                 "mcp-server/src/judge-providers.v1.json"]
        for rel in paths:
            target = repository / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((REPO / rel).read_bytes())
        target = repository / "ops/config/jev-call-sites.v1.json"
        target.write_bytes(self.registry.read_bytes())
        paths.append(str(target.relative_to(repository)))
        def git(*args):
            return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repository,
                                  env=ENV, capture_output=True, text=True, check=True)
        git("init", "-q")
        git("add", *paths)
        message = self.root / "fixture-commit.txt"
        message.write_text("Offline worktree fixture\n")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-F", str(message))
        worktrees = [self.root / "one", self.root / "two"]
        for index, tree in enumerate(worktrees):
            git("worktree", "add", "-q", "-b", f"fixture-{index}", str(tree))
        program = '''import io,json,os,sys
sys.path.insert(0, "ops"); import typesafe_client as c
for flag in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE", "CARR_JEV_WORKER"):
    os.environ.pop(flag,None)
c.JEV_COST_CONFIG["daily_paid_call_cap"]=1
c._launch_spend_alert_worker=lambda *args: None
sent=[]
class Response(io.BytesIO):
    status=200
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
def transport(*args,**kwargs):
    sent.append(1)
    return Response(json.dumps({"model":"m","answers":{"q":{"type":"noul","noul":0.8}},
                               "usage":{"input_tokens":1,"output_tokens":1}}).encode())
c.urllib.request.urlopen=transport
refused=False
try: c.ask("fixture",{"q":c.noul("fixture")},caller="hook_site",session_id="fixture",
           api_key="synthetic",cache_ttl_seconds=0)
except c.JevCallRefused: refused=True
print(json.dumps({"paid":len(sent),"refused":refused,"budget":c.JEV_DAILY_CAP_LOG}))'''
        processes = [subprocess.Popen([sys.executable, "-c", program], cwd=tree, env=ENV,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for tree in worktrees]
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            outputs.append((process.returncode, stdout, stderr))
        results = []
        for code, stdout, stderr in outputs:
            self.assertEqual(code, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(row["paid"] for row in results), 1)
        self.assertEqual(sum(row["refused"] for row in results), 1)
        self.assertEqual({row["budget"] for row in results}, {str(repository.resolve() / "out/jev-calls.jsonl")})

    def test_acceptance_counts_requests_refusals_cache_and_paid_transport_separately(self):
        cache = self.root / "acceptance-cache.sqlite3"
        counts = {"attempted": 0, "refused": 0, "cached": 0, "paid": 0}
        with patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1):
            for state, caller in (("s", "hook_site"), ("s", "hook_site"),
                                  ("new", "hook_site"), ("new", "unknown")):
                counts["attempted"] += 1
                try:
                    result = client.ask(state, {"q": client.noul("Fixture")}, api_key="offline-fixture",
                                        caller=caller, session_id="s", cache_path=cache,
                                        cache_ttl_seconds=60, calls_log=str(self.log))
                except client.JevCallRefused:
                    counts["refused"] += 1
                else:
                    counts["cached"] += result.get("cache_hit") is True
            counts["paid"] = len(self.requests)
        self.assertEqual(counts, {"attempted": 4, "refused": 2, "cached": 1, "paid": 1})
        print("offline admission acceptance: " + json.dumps(counts, sort_keys=True))

    def paid_server_ask(self, runner, **overrides):
        kwargs = dict(model="jev-1.13.0", facets=[], purpose="call", session_id="s", timeout=2,
                      transport_mode="paid_once", runner=runner, caller="hook_site")
        kwargs.update(overrides)
        return client.server_ask("s", {"q": client.noul("Fixture")}, **kwargs)

    def daily_attempts(self):
        path = Path(str(self.log) + ".daily-cap.sqlite3")
        if not path.exists():
            return 0
        with client.closing(client.sqlite3.connect(str(path))) as db:
            row = db.execute("SELECT attempts FROM daily_cap WHERE day='2026-10-04'").fetchone()
        return row[0] if row else 0

    def test_public_worker_reservation_is_observed_only_by_matching_session(self):
        calls, runner = WorkerBreakerTests.worker(self, failures=False)
        with client.capture_paid_reservations(caller='hook_site', session_id='s', run_id='review') as receipt:
            self.assertIsNone(self.paid_server_ask(runner)[1])
            self.assertIsNone(self.paid_server_ask(runner, session_id='other')[1])
        self.assertEqual(sum(receipt['utc_days'].values()), 1)
        self.assertEqual(self.daily_attempts(), 2)
        self.assertEqual(calls, ['paid_once', 'paid_once'])

    def test_public_worker_402_holds_the_next_paid_entry(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, "",
                'TOOL ERROR {"error":"jev_upstream_failed","status":402,"reason":"http_status"}')
        upstream = {}
        self.assertEqual(self.paid_server_ask(runner, upstream=upstream), (None, "vendor_failed_at_worker"))
        self.assertIsInstance(upstream["refusal"], client.TypeSafeError)
        with self.assertRaises(client.JevCallRefused):
            self.paid_server_ask(runner)
        self.assertEqual(len(calls), 1)

    def test_public_worker_shares_budget_and_vendor_hold_with_direct_entry(self):
        calls, runner = WorkerBreakerTests.worker(self, failures=False)
        for hold in (False, True):
            with self.subTest(hold=hold), \
                    patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=0 if not hold else 1000):
                if hold:
                    client._open_hold("credit_hold", 60)
                with self.assertRaises(client.JevCallRefused):
                    self.paid_server_ask(runner)
        self.assertEqual(calls, [])
        self.assertEqual(self.requests, [])

    def test_worker_route_signs_with_the_admission_secret_not_the_vendor_key(self):
        captured = []
        def runner(argv, **kwargs):
            captured.append(json.loads(argv[3]))
            return subprocess.CompletedProcess(argv, 0, json.dumps({**ANSWER, "ok": True,
                                                                     "receipt_id": "w"}), "")
        served, error = self.paid_server_ask(runner)
        self.assertIsNone(error)
        self.assertEqual(served["server_receipt"]["receipt_id"], "w")
        client.read_api_key.assert_not_called()
        _, payload, signature = captured[0]["idempotency_key"].split(".")
        self.assertEqual(signature, client.hmac.new(b"offline-admission", payload.encode(),
                                                    client.hashlib.sha256).hexdigest())

    def test_missing_admission_secret_is_a_transport_gap_that_spends_no_slot(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            raise AssertionError("no Worker dispatch without an admission secret")
        with patch.object(client, "read_admission_secret",
                          side_effect=client.TypeSafeError("no admission secret")):
            self.assertEqual(self.paid_server_ask(runner), (None, "admission_secret_missing"))
        self.assertEqual(calls, [])
        self.assertEqual(self.daily_attempts(), 0)

    def test_worker_admission_refusal_is_named_and_never_paid_again_direct(self):
        def runner(argv, **kwargs):
            if json.loads(argv[3]).get("transport_mode") == "cache_only":
                return subprocess.CompletedProcess(argv, 1, "", '{"error":"jev_cache_miss"}')
            return subprocess.CompletedProcess(argv, 1, "", 'TOOL ERROR {"error":"jev_admission_required"}')
        upstream = {}
        self.assertEqual(self.paid_server_ask(runner, upstream=upstream),
                         (None, "admission_refused_at_worker"))
        self.assertIsInstance(upstream["refusal"], client.TypeSafeError)
        with self.assertRaises(client.TypeSafeError):
            WorkerBreakerTests.ask_worker(self, "s", runner)
        self.assertEqual(self.requests, [], "a refused proof must not buy a second, direct attempt")
        self.assertEqual(self.daily_attempts(), 2, "one reservation per entry, none for a fallback")

    def test_worker_vendor_failure_policy_runs_once_per_failure(self):
        decisions = []
        original = client._after_worker_vendor_failure
        def counted(*args):
            decisions.append(args)
            return original(*args)
        calls, runner = WorkerBreakerTests.worker(self, failures=True)
        with patch.object(client, "_after_worker_vendor_failure", counted):
            WorkerBreakerTests.ask_worker(self, "s", runner)
        self.assertEqual(calls, ["cache_only", "paid_once"])
        self.assertEqual(len(decisions), 1)

    def test_worker_paid_call_is_admitted_once(self):
        admissions = []
        original = client._admit_paid_call
        def counted(*args):
            admissions.append(args)
            return original(*args)
        calls, runner = WorkerBreakerTests.worker(self, failures=False)
        with patch.object(client, "_admit_paid_call", counted):
            WorkerBreakerTests.ask_worker(self, "s", runner)
        self.assertEqual(calls, ["cache_only", "paid_once"])
        self.assertEqual(len(admissions), 1)

    def test_python_reservation_proof_is_accepted_once_by_worker(self):
        captured = []
        def runner(argv, **kwargs):
            captured.append(json.loads(argv[3]))
            return subprocess.CompletedProcess(argv, 0, json.dumps({**ANSWER, "ok": True,
                                                                     "receipt_id": "w"}), "")
        self.paid_server_ask(runner)
        script = '''import { jevAskBinding } from "./mcp-server/src/jev-call-receipt.js";
let text = ""; for await (const chunk of process.stdin) text += chunk;
const request = JSON.parse(text); let calls = 0, consumed = false;
const ask = jevAskBinding({ TYPESAFE_API_KEY: "vendor-fixture", JEV_ADMISSION_SECRET: "offline-admission" },
  async () => { calls++; return new Response(JSON.stringify({model:"m",answers:{q:{noul:0.8}}})); },
  {cache:null, reserveAttempt:async () => {
    if(consumed) throw Error("already consumed"); consumed = true;
    return {key:"fixture",receipt_id:"fixture"};
  }});
await ask(request); let refused = false;
try { await ask(request); } catch { refused = true; }
console.log(JSON.stringify({calls,refused}));'''
        proc = subprocess.run(["node", "--input-type=module", "-e", script], cwd=REPO,
                              input=json.dumps(captured[0]), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(proc.stdout), {"calls": 1, "refused": True})

    def test_public_worker_transport_cannot_bypass_admission(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps({**ANSWER, "ok": True,
                                                                     "receipt_id": "w"}), "")
        for cap in (1000, 0):
            with self.subTest(cap=cap), patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=cap):
                with self.assertRaises(client.JevCallRefused):
                    client.server_ask("s", {"q": client.noul("Fixture")}, model="jev-1.13.0",
                                      facets=[], purpose="call", session_id="s", timeout=2,
                                      transport_mode="paid_once", runner=runner)
        self.assertEqual(calls, [])

    def test_vendor_hold_blocks_all_local_entries(self):
        client._open_hold("credit_hold", 60)
        for entry in (client.ask, client._ask_jev):
            with self.subTest(entry=entry.__name__), self.assertRaises(client.JevCallRefused):
                entry("s", {"q": client.noul("Fixture")}, api_key="offline-fixture",
                      caller="hook_site", session_id="s", cache_ttl_seconds=0)
        self.assertEqual(self.requests, [])

    def test_worker_fallback_reserves_again_and_cannot_overrun_last_slot(self):
        calls, runner = WorkerBreakerTests.worker(self, failures=True)
        with patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1):
            with self.assertRaises(client.JevCallRefused):
                WorkerBreakerTests.ask_worker(self, "s", runner)
        self.assertEqual(calls, ["cache_only", "paid_once"])
        self.assertEqual(self.requests, [])


# Files that make up the transport itself, or call it only through a site
# that is registered. Anything else that reaches a paid call must be listed.
INFRASTRUCTURE = {"ops/typesafe_client.py", "ops/jev_judge.py", "ops/jev_semantic.py",
                  "tools/judge/interface.py"}


# ask-jev in a dispatch position: an in-Worker callTool, `run.sh call ask-jev`
# and `local-verb.mjs ask-jev` in shell, or the same words as an argv list.
_ARG = r"""["']?(?:\s*,\s*["']|\s+)"""
VERB_DISPATCH = re.compile(
    r"""callTool(?:Fn)?\([^)]{0,200}?["']ask-jev["']"""
    rf"""|run\.sh{_ARG}call{_ARG}["']?ask-jev\b"""
    rf"""|local-verb(?:\.mjs)?{_ARG}["']?ask-jev\b""")


def transport_sources(root, paths):
    """Paid endpoint literals and Worker dispatches, across launcher languages."""
    found = set()
    for rel in paths:
        if (Path(rel).suffix not in {".py", ".sh", ".js", ".mjs"} or
                "selftest" in rel or "/fixtures/" in rel or "/test" in rel or rel.startswith("test")):
            continue
        source = (root / rel).read_text(encoding="utf-8", errors="replace")
        source = re.sub(r"(?m)^\s*(?:#|//).*?$", "", source)
        if "https://api.typesafe.ai/" in source or VERB_DISPATCH.search(source):
            found.add(rel)
    return found


# What each registered control must visibly do in its source. A label whose
# marker is absent claims a control the source does not implement.
CONTROL_MARKERS = {
    "shared_admission": r"= _reserve_paid_call\([\s\S]{0,200}?= _admission_token\(",
    "worker_reservation_proof": r"verifyAdmission\(",
    "worker_runtime_class": r'"ask-jev"[\s\S]{0,400}"app_runtime"',
}


def unimplemented_controls(root, transport_controls):
    """Registered sources whose source text lacks their control's marker."""
    return sorted(rel for rel, control in transport_controls.items()
                  if not re.search(CONTROL_MARKERS.get(control, r"(?!)"),
                                   (root / rel).read_text(encoding="utf-8")))


def paid_call_sources():
    """Every tracked non-test Python file that invokes the Jev client or judge()."""
    tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.split()
    found = set()
    for rel in tracked:
        if (rel in INFRASTRUCTURE or "selftest" in rel or "/fixtures/" in rel or rel.startswith("evals/")
                or "/tests/" in rel or rel.startswith("tests/") or Path(rel).name.startswith("test_")):
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if not re.search(r"typesafe|jev_judge", text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        paid_methods = {"ask", "_ask_jev", "judge", "server_ask"}
        imported = {alias.asname or alias.name
                    for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    and (node.module or "").split(".")[-1] in {"typesafe_client", "jev_judge"}
                    for alias in node.names if alias.name in paid_methods}
        for node in ast.walk(tree):
            # Over-inclusive on purpose: any .ask()/.judge()/.server_ask() call in
            # a file that loads the client or the judge counts as a paid path.
            if (isinstance(node, ast.Call) and
                    ((isinstance(node.func, ast.Attribute) and node.func.attr in paid_methods) or
                     (isinstance(node.func, ast.Name) and node.func.id in imported))):
                found.add(rel)
                break
    return found


class RegistryCoverageTests(unittest.TestCase):
    def test_inventory_detects_raw_transports_in_every_launcher_language(self):
        examples = {"new.mjs": 'fetch("https://api.typesafe.ai/v1/systemone")',
                    "new.sh": 'curl https://api.typesafe.ai/v1/systemone',
                    "new.py": 'requests.post("https://api.typesafe.ai/v1/systemone")',
                    "worker.mjs": 'callTool(env, actor, "ask-jev", {})',
                    "verb.sh": "./run.sh call ask-jev '{\"x\":1}'",
                    "local.sh": 'node mcp-server/local-verb.mjs ask-jev "$json"',
                    "quoted.sh": "run.sh call 'ask-jev' '{}'",
                    "argv.py": 'subprocess.run(["./run.sh", "call", "ask-jev", payload])'}
        prose = {"doc.py": '"""Reads through ./run.sh call; every receipt has its ask-jev row."""',
                 "pattern.py": "re.search(r'callTool(?:Fn)?\\(.*[\"]ask-jev[\"]', line)"}
        with tempfile.TemporaryDirectory() as root:
            for name, source in {**examples, **prose}.items():
                Path(root, name).write_text(source)
            self.assertEqual(transport_sources(Path(root), {**examples, **prose}), set(examples))

    def test_control_labels_need_the_control_in_their_source(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "proof.js").write_text("await verifyAdmission(secret, token, session, now);")
            Path(root, "plain.py").write_text('run(["./run.sh", "call", "ask-jev", str(uuid.uuid4())])')
            labels = {"proof.js": "worker_reservation_proof", "plain.py": "worker_reservation_proof"}
            self.assertEqual(unimplemented_controls(Path(root), labels), ["plain.py"])
            self.assertEqual(unimplemented_controls(Path(root), {"proof.js": "decorative"}), ["proof.js"])

    def test_scan_covers_paid_imports_and_aliases_without_counting_unrelated_names(self):
        examples = {
            "attribute.py": "import typesafe_client as ts\nts.ask({}, {})",
            "direct.py": "from typesafe_client import ask, noul\nask({}, {'q': noul('fixture')})",
            "alias.py": "from ops.typesafe_client import ask as paid\npaid({}, {})",
            "judge.py": "from jev_judge import judge\njudge({}, {})",
            "judge_alias.py": "from ops.jev_judge import judge as assess\nassess({}, {})",
            "server_alias.py": "from typesafe_client import server_ask as remote\nremote({}, {})",
            "unused.py": "from typesafe_client import ask\nvalue = 1",
            "unrelated.py": "from other import ask\nfrom typesafe_client import noul\nask('fixture')",
        }
        with tempfile.TemporaryDirectory() as root:
            for name, source in examples.items():
                Path(root, name).write_text(source)
            with patch.dict(globals(), REPO=Path(root)), patch.object(
                    subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "\n".join(examples), "")):
                self.assertEqual(paid_call_sources(), set(examples) - {"unused.py", "unrelated.py"})

    def test_production_registry_is_valid(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        self.assertLessEqual(registry["hourly_paid_call_cap"] * 24,
                             client.JEV_COST_CONFIG["daily_paid_call_cap"] * 24)
        for entry in registry["sites"].values():
            self.assertLessEqual(entry["daily_budget"], client.JEV_COST_CONFIG["daily_paid_call_cap"],
                                 "each site limit sits beneath the shared admission cap")

    def test_every_paid_call_source_is_registered(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        registered = {s for e in registry["sites"].values() for s in e["sources"]}
        missing = sorted(paid_call_sources() - registered)
        self.assertEqual(missing, [], "a new paid Jev call path needs an entry in "
                         "ops/config/jev-call-sites.v1.json (caller, trigger, budgets, owner, value)")

    def test_every_transport_is_registered_with_a_control_its_source_implements(self):
        controls = json.loads(REGISTRY_PATH.read_text())["transport_sources"]
        tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                                 text=True, check=True).stdout.splitlines()
        self.assertEqual(sorted(transport_sources(REPO, tracked) - set(controls)), [],
                         "new paid transport bypass: use typesafe_client admission before dispatch; "
                         "register its source and control in jev-call-sites.v1.json")
        self.assertEqual(unimplemented_controls(REPO, controls), [],
                         "a registered transport control is not implemented by its source")

    def test_every_registered_source_exists(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        for entry in registry["sites"].values():
            for rel in entry["sources"]:
                self.assertTrue((REPO / rel).is_file(), f"{entry['caller']}: {rel} does not exist")

    def test_registered_caller_names_match_their_source_module(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        for caller, entry in registry["sites"].items():
            if caller.endswith("*"):
                continue
            stems = {Path(s).stem for s in entry["sources"]}
            named = caller in stems or any(
                f'"{caller}"' in (REPO / s).read_text(encoding="utf-8", errors="replace")
                for s in entry["sources"])
            self.assertTrue(named, f"{caller}: the caller is the calling module's file name, "
                                   "or an explicit caller= string in one of its sources")


if __name__ == "__main__":
    unittest.main()
