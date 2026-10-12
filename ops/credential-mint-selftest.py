#!/usr/bin/env python3
"""Hermetic acceptance test for the credential mint lane (lib/credential_mint.py,
ops/credential-mint.py).

No network, no provider, no record layer, no Codex CLI: a fake HTTP server that
keeps real token state for Cloudflare, Neon and OpenAI, a fixed clock the test
advances by hand, a fake loop board, a fake `codex` runner, and a scratch HOME so
every credential file the lane writes lands in a temporary directory.

What it holds the lane to: rotation happens only inside the window; a minted
child is verified with ITSELF before anything is stored; a child that fails
verify is revoked and the previous one kept; the store goes through
lib/token_store.py into the file each name belongs in; Codex gets the new key
on stdin; the previous child is revoked, and a revoke that fails is retried the
next night; an absent root grant is never a live call and files exactly one
loop per provider, deduplicated by the loop board; a refused grant re-files it;
a child is minted only on its own machine; and no credential value ever reaches
stdout, a receipt or the state file.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import credential_mint as cm  # noqa: E402
from lib import token_store  # noqa: E402
from lib.credential_file import read_env_file  # noqa: E402
from lib.record_call import VerbResult  # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def check(label: str, ok: bool, detail: Any = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label} {detail!r}")


T0 = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
ROOTS = {"CLOUDFLARE_TOKEN_ROOT": "mintfixture-root-cloudflare",
         "NEON_ORG_ROOT_KEY": "mintfixture-root-neon",
         "OPENAI_ADMIN_KEY": "mintfixture-root-openai"}


class FakeProviders:
    """Just enough of three control planes to keep honest token state."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.active: dict[str, set[str]] = {"cloudflare": set(), "neon": set(), "openai": set()}
        self.ids: dict[str, dict[str, str]] = {"cloudflare": {}, "neon": {}, "openai": {}}
        self.minted: list[str] = []
        self.payloads: list[Any] = []
        self.bad_children: set[str] = set()   # values whose verify must fail
        self.fail_revoke: set[str] = set()    # ids whose DELETE must fail
        self.refuse_root = False
        self.counter = 0

    def _mint(self, provider: str) -> tuple[str, str]:
        self.counter += 1
        child_id, value = f"{provider}-id-{self.counter}", f"mintfixture-{provider}-child-{self.counter}"
        self.active[provider].add(value)
        self.ids[provider][child_id] = value
        self.minted.append(value)
        return child_id, value

    def _revoke(self, provider: str, child_id: str) -> int:
        if child_id in self.fail_revoke:
            return 500
        value = self.ids[provider].pop(child_id, None)
        if value is None:
            return 404
        self.active[provider].discard(value)
        return 200

    def __call__(self, method: str, url: str, headers: Any, body: Any) -> tuple[int | None, Any]:
        token = (headers.get("Authorization") or "").removeprefix("Bearer ") or None
        self.calls.append((method, url, token))
        parts = urlsplit(url)
        path = parts.path
        is_root = token in ROOTS.values()
        if is_root and self.refuse_root and method != "GET":
            return 401, None
        if parts.netloc == "api.cloudflare.com":
            if path.endswith("/user/tokens/verify"):
                ok = token in self.active["cloudflare"] or token == ROOTS["CLOUDFLARE_TOKEN_ROOT"]
                ok = ok and token not in self.bad_children
                return (200, {"result": {"status": "active"}}) if ok else (401, None)
            if path.endswith("/user/tokens/permission_groups"):
                if self.refuse_root:
                    return 401, None
                return 200, {"result": [{"id": "pg-w", "name": "Workers Scripts Write"},
                                        {"id": "pg-a", "name": "Account Settings Read"},
                                        {"id": "pg-x", "name": "Zone Read"}]}
            if method == "POST" and path.endswith("/user/tokens"):
                self.payloads.append(body)
                child_id, value = self._mint("cloudflare")
                return 200, {"result": {"id": child_id, "value": value}}
            if method == "DELETE":
                return self._revoke("cloudflare", path.rsplit("/", 1)[1]), None
        if parts.netloc == "console.neon.tech":
            if method == "GET" and path.endswith("/projects"):
                ok = (token in self.active["neon"] or token == ROOTS["NEON_ORG_ROOT_KEY"]) \
                    and token not in self.bad_children and "org_id=org-dry-dew-75906281" in parts.query
                return (200, {"projects": []}) if ok else (401, None)
            if method == "POST" and path.endswith("/api_keys"):
                child_id, value = self._mint("neon")
                return 200, {"id": child_id, "key": value}
            if method == "DELETE":
                return self._revoke("neon", path.rsplit("/", 1)[1]), None
        if parts.netloc == "api.openai.com":
            if path == "/v1/models":
                ok = token in self.active["openai"] and token not in self.bad_children
                return (200, {"data": []}) if ok else (401, None)
            if method == "GET" and path == "/v1/organization/projects":
                return 200, {"data": [{"id": "proj-1", "name": "carr-system", "status": "active"}]}
            if method == "POST" and path.endswith("/service_accounts"):
                child_id, value = self._mint("openai")
                return 200, {"id": child_id, "api_key": {"value": value, "id": "key-" + child_id}}
            if method == "DELETE":
                return self._revoke("openai", path.rsplit("/", 1)[1]), None
        return 599, None


class FakeBoard:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.loops: list[dict] = []
        self.readable = True

    def __call__(self, verb: str, args: Any) -> VerbResult:
        self.calls.append((verb, dict(args)))
        if verb == "loop-board":
            if not self.readable:
                return VerbResult(verb, "transient", detail="timed out")
            hits = [loop for loop in self.loops if args["search"] in loop["label"]]
            return VerbResult(verb, "ok", reply={"loops": hits})
        if verb == "add-loop":
            self.loops.append({"number": str(900 + len(self.loops)), "version": 1,
                               "label": args["body"].splitlines()[0],
                               "blocker_detail": args["blocker_detail"]})
            return VerbResult(verb, "ok", reply={"ok": True})
        if verb == "update-loop":
            for loop in self.loops:
                if loop["number"] == args["number"]:
                    loop["blocker_detail"] = args["blocker_detail"]
                    loop["version"] += 1
            return VerbResult(verb, "ok", reply={"ok": True})
        if verb == "close-loop":
            self.loops = [loop for loop in self.loops if loop["number"] != args["number"]]
            return VerbResult(verb, "ok", reply={"ok": True})
        return VerbResult(verb, "refused")

    def writes(self) -> list[str]:
        return [verb for verb, _ in self.calls if verb != "loop-board"]


@dataclass
class Clock:
    now: datetime = T0

    def __call__(self) -> datetime:
        return self.now


class FakeCodex:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail = False

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), kw.get("input")))
        return subprocess.CompletedProcess(argv, 1 if self.fail else 0, stdout="", stderr="")


class World:
    """One scratch HOME + out/ directory, and the fakes wired into a Minter."""

    def __init__(self, tmp: Path, *, machine: str = "studio", roots: tuple[str, ...] = ()):
        self.home = tmp / "home"
        self.home.mkdir()
        os.environ["HOME"] = str(self.home)
        for name in roots:
            token_store.store(name, ROOTS[name])
        self.http, self.board, self.clock, self.codex = FakeProviders(), FakeBoard(), Clock(), FakeCodex()
        self.state, self.receipts = tmp / "out" / "state.json", tmp / "out" / "receipts.jsonl"
        self.machine = machine
        self.plan = cm.load_plan()

    def night(self) -> list[dict]:
        minter = cm.Minter(self.plan, self.machine, http=self.http, clock=self.clock,
                           run=self.codex, call_verb=self.board,
                           state_path=self.state, receipts_path=self.receipts)
        return minter.run_once()

    def stored(self, name: str) -> str | None:
        return token_store.read(name)

    def saved_state(self) -> dict:
        return json.loads(self.state.read_text())


@contextlib.contextmanager
def world(**kw: Any):
    old_home = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as raw:
        try:
            yield World(Path(raw), **kw)
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home


def outcomes(receipts: list[dict], credential: str) -> list[str]:
    return [f"{r['step']}:{r['outcome']}" for r in receipts if r["credential"] == credential]


# ── the plan ────────────────────────────────────────────────────────────────

def test_plan_is_consistent_and_refuses_disagreement() -> None:
    print("plan: grant file and inventory agree, and a disagreement is refused")
    plan = cm.load_plan()
    inventory = json.loads(cm.INVENTORY_PATH.read_text())["credentials"]
    check("every inventory row is self_mintable_from XOR holder_only",
          all(bool(r.get("self_mintable_from")) != bool(r.get("holder_only")) for r in inventory))
    check("grok-cli-auth row exists and is holder_only",
          any(r["name"] == "grok-cli-auth" and r.get("holder_only") for r in inventory))
    names = {g.name for g in plan.grants}
    check("the five providers are each one grant", names == {"cloudflare", "neon", "openai", "xai", "github-app"}, names)
    check("rotation window is seven days", plan.rotate_within_days == 7)

    def refuses(mutate) -> bool:
        with tempfile.TemporaryDirectory() as raw:
            doc = json.loads(cm.INVENTORY_PATH.read_text())
            mutate(doc)
            path = Path(raw) / "inv.json"
            path.write_text(json.dumps(doc))
            try:
                cm.load_plan(cm.GRANTS_PATH, path)
            except cm.PlanError:
                return True
            return False

    def strip_marker(doc):
        doc["credentials"][0].pop("self_mintable_from")

    def both_markers(doc):
        doc["credentials"][1]["self_mintable_from"] = "cloudflare"

    def unknown_grant(doc):
        doc["credentials"][1].pop("holder_only")
        doc["credentials"][1]["self_mintable_from"] = "nonexistent"

    def unlisted_child(doc):
        row = next(r for r in doc["credentials"] if r["name"] == "grok-cli-auth")
        row.pop("holder_only")
        row["self_mintable_from"] = "xai"

    check("a row with neither marker is refused", refuses(strip_marker))
    check("a row with both markers is refused", refuses(both_markers))
    check("a grant name that does not exist is refused", refuses(unknown_grant))
    check("a row its grant does not list is refused", refuses(unlisted_child))


# ── an absent grant ─────────────────────────────────────────────────────────

def test_missing_grant_is_never_a_live_call_and_files_one_loop_each() -> None:
    print("missing grant: no live call, one deduplicated loop per provider")
    with world() as w:
        receipts = w.night()
        check("no HTTP call at all with every grant absent", w.http.calls == [], w.http.calls)
        adds = [args for verb, args in w.board.calls if verb == "add-loop"]
        markers = sorted(a["body"].split("]")[0] + "]" for a in adds)
        check("one loop per provider with children on this machine",
              markers == ["[credential-root-grant:cloudflare]", "[credential-root-grant:github-app]",
                          "[credential-root-grant:neon]", "[credential-root-grant:openai]"], markers)
        cf = next(a for a in adds if "credential-root-grant:cloudflare" in a["body"])
        check("the loop names the one-time command",
              "tools/store-token.py CLOUDFLARE_TOKEN_ROOT" in cf["body"] and
              "tools/store-token.py CLOUDFLARE_TOKEN_ROOT" in cf["blocker_detail"])
        check("the loop is Joe's, human_only, in the system domain",
              cf["owner"] == "Joe" and cf["blocker"] == "human_only" and cf["domain"] == "system")
        check("the deferred xAI grant files nothing", not any("xai" in a["body"] for a in adds))
        check("every absent grant leaves a receipt",
              sum(r["outcome"] == "absent" for r in receipts) == 4)

        w.board.calls.clear()
        w.night()
        check("a second night files nothing new (deduplicated by the loop board)",
              w.board.writes() == [], w.board.writes())
        check("dedup asked the loop board, not a local file",
              [verb for verb, _ in w.board.calls].count("loop-board") == 4)

        w.board.loops[0]["blocker_detail"] = "stale detail from an older plan"
        w.board.calls.clear()
        w.night()
        updates = [args for verb, args in w.board.calls if verb == "update-loop"]
        check("a changed detail updates the existing loop instead of filing a second",
              w.board.writes() == ["update-loop"] and updates[0]["number"] == w.board.loops[0]["number"]
              and updates[0]["base_version"] == 1, w.board.calls)

        w.board.readable = False
        w.board.calls.clear()
        w.board.loops.clear()
        w.night()
        check("an unreadable board files nothing rather than risk a duplicate",
              w.board.writes() == [], w.board.writes())


def test_placing_the_grant_closes_its_loop() -> None:
    print("grant placed: the first rotation closes the loop that asked for it")
    with world() as w:
        w.night()
        cf_loop = next(loop for loop in w.board.loops if "credential-root-grant:cloudflare" in loop["label"])
        token_store.store("CLOUDFLARE_TOKEN_ROOT", ROOTS["CLOUDFLARE_TOKEN_ROOT"])
        w.board.calls.clear()
        w.night()
        closes = [args for verb, args in w.board.calls if verb == "close-loop"]
        check("exactly the cloudflare loop is closed, done, with an outcome",
              len(closes) == 1 and closes[0]["number"] == cf_loop["number"]
              and closes[0]["resolution"] == "done" and "placed" in closes[0]["outcome"], closes)
        w.board.calls.clear()
        w.night()
        check("a night with nothing due reads no board for a present grant",
              not any("credential-root-grant:cloudflare" in str(a) for _, a in w.board.calls))


def test_github_app_key_present_files_nothing() -> None:
    print("github-app: already self-minting; a present key files nothing")
    with world() as w:
        key = token_store.destination("CLOUDFLARE_API_TOKEN").path.parent / "github-app.pem"
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text("placeholder, never read by the lane\n")
        w.night()
        bodies = [a["body"] for v, a in w.board.calls if v == "add-loop"]
        check("no github-app loop when its key file exists",
              not any("credential-root-grant:github-app" in b for b in bodies), bodies)


# ── a rotation ──────────────────────────────────────────────────────────────

def test_first_mint_verifies_with_the_new_child_and_stores_it() -> None:
    print("first mint: mint, verify with the child, store, record")
    with world(roots=("CLOUDFLARE_TOKEN_ROOT",)) as w:
        receipts = w.night()
        child = w.http.minted[0]
        verify_calls = [c for c in w.http.calls if c[1].endswith("/user/tokens/verify")]
        check("verify was called with the NEW child, not the root",
              bool(verify_calls) and verify_calls[-1][2] == child, verify_calls)
        check("the child landed as CLOUDFLARE_API_TOKEN in tokens.env",
              w.stored("CLOUDFLARE_API_TOKEN") == child)
        tokens = token_store.destination("CLOUDFLARE_API_TOKEN").path
        check("tokens.env is 0600", stat.S_IMODE(tokens.stat().st_mode) == 0o600)
        check("the root grant line survived the child write",
              read_env_file(tokens).get("CLOUDFLARE_TOKEN_ROOT") == ROOTS["CLOUDFLARE_TOKEN_ROOT"])
        payload = w.http.payloads[0]
        check("permission groups resolved to ids, scoped to the CARR account",
              payload["policies"][0]["permission_groups"] == [{"id": "pg-w"}, {"id": "pg-a"}]
              and list(payload["policies"][0]["resources"]) ==
              ["com.cloudflare.api.account.12ccca77eb49142a6be8eb84c0d6a3a0"], payload)
        check("the provider-side expiry is the ttl", payload["expires_on"] == "2027-01-10T08:00:00Z",
              payload["expires_on"])
        entry = w.saved_state()["credentials"]["cloudflare-deploy-token"]
        check("state records the child id and a 90-day expiry",
              entry["child_id"] == "cloudflare-id-1" and entry["expires_at"].startswith("2027-01-10"), entry)
        check("a hand-placed predecessor is left in place and said so",
              "revoke_previous:unmanaged_previous_left_in_place" in outcomes(receipts, "cloudflare-deploy-token"))


def test_schedule_rotates_only_inside_the_window_and_revokes_the_previous() -> None:
    print("schedule: nothing early, rotate inside seven days, revoke the previous")
    with world(roots=("CLOUDFLARE_TOKEN_ROOT",)) as w:
        w.night()
        first = w.http.minted[0]
        w.http.calls.clear()
        w.clock.now = T0 + timedelta(days=82, hours=23)   # 7 days 1 hour left
        w.night()
        check("no live call outside the window", w.http.calls == [], w.http.calls)
        w.clock.now = T0 + timedelta(days=83, hours=1)    # 6 days 23 hours left
        receipts = w.night()
        second = w.http.minted[-1]
        check("a second child was minted inside the window", len(w.http.minted) == 2)
        check("the store now holds the second child", w.stored("CLOUDFLARE_API_TOKEN") == second)
        check("the previous child was revoked at the provider",
              first not in w.http.active["cloudflare"] and second in w.http.active["cloudflare"])
        check("the revoke used the root, by id",
              ("DELETE", f"{cm.Cloudflare.API}/user/tokens/cloudflare-id-1", ROOTS["CLOUDFLARE_TOKEN_ROOT"])
              in w.http.calls)
        check("the rotation's receipts read mint, verify, store, revoke",
              outcomes(receipts, "cloudflare-deploy-token") ==
              ["mint:minted", "verify:verified", "store:stored_CLOUDFLARE_API_TOKEN",
               "revoke_previous:revoked"], outcomes(receipts, "cloudflare-deploy-token"))


def test_verify_failure_revokes_the_new_child_and_keeps_the_old() -> None:
    print("verify failure: the new child is revoked, the previous one stays")
    with world(roots=("CLOUDFLARE_TOKEN_ROOT",)) as w:
        w.night()
        first = w.stored("CLOUDFLARE_API_TOKEN")
        state_before = w.saved_state()["credentials"]
        w.http.bad_children.add("mintfixture-cloudflare-child-2")
        w.clock.now = T0 + timedelta(days=85)
        receipts = w.night()
        check("the store still holds the previous child", w.stored("CLOUDFLARE_API_TOKEN") == first)
        check("the failed child was revoked", "mintfixture-cloudflare-child-2" not in w.http.active["cloudflare"])
        check("the previous child is still active", first in w.http.active["cloudflare"])
        check("state still names the previous child", w.saved_state()["credentials"] == state_before)
        check("the receipt says so",
              "verify:failed_new_child_revoked_previous_kept" in outcomes(receipts, "cloudflare-deploy-token"))


def test_store_failure_revokes_the_new_child() -> None:
    print("store failure: nothing half-written, the new child is revoked")
    with world(roots=("NEON_ORG_ROOT_KEY",)) as w:
        db_env = token_store.destination("NEON_API_KEY").path
        db_env.mkdir(parents=True)  # a directory where the file should be: every write fails
        receipts = w.night()
        check("the minted child was revoked", w.http.active["neon"] == set())
        check("no state was recorded for it", "neon-api-key" not in w.saved_state()["credentials"])
        check("the receipt names the store step",
              "store:failed_new_child_revoked_previous_kept" in outcomes(receipts, "neon-api-key"))


def test_failed_revoke_is_retried_the_next_night() -> None:
    print("revoke retry: a child that cannot be revoked is retried until it is")
    with world(roots=("NEON_ORG_ROOT_KEY",)) as w:
        w.night()
        w.http.fail_revoke.add("neon-id-1")
        w.clock.now = T0 + timedelta(days=84)
        w.night()
        pending = w.saved_state()["pending_revoke"]
        check("the unrevoked child is queued by id", [p["child_id"] for p in pending] == ["neon-id-1"], pending)
        w.http.fail_revoke.clear()
        w.http.calls.clear()
        w.clock.now = T0 + timedelta(days=85)
        receipts = w.night()
        check("the next night revokes it", "mintfixture-neon-child-1" not in w.http.active["neon"])
        check("and empties the queue", w.saved_state()["pending_revoke"] == [])
        check("without minting again", len(w.http.minted) == 2)
        check("the retry is receipted", "retry_revoke:revoked" in outcomes(receipts, "neon-api-key"))


def test_neon_child_lands_in_db_env_sourceably() -> None:
    print("neon: the child lands in db.env, readable by both parsers")
    with world(roots=("NEON_ORG_ROOT_KEY",)) as w:
        db_env = token_store.destination("NEON_API_KEY").path
        db_env.parent.mkdir(parents=True, exist_ok=True)
        db_env.write_text("CARR_DB_JOBS_URL='postgresql://u:p@h/d?a=1&b=2'\n")  # ci-secret-scan: allow
        w.night()
        values = read_env_file(db_env)
        check("NEON_API_KEY is the new child", values.get("NEON_API_KEY") == w.http.minted[0])
        check("the neighbouring line is untouched", values.get("CARR_DB_JOBS_URL") == "postgresql://u:p@h/d?a=1&b=2")
        parsed = subprocess.run(["/bin/zsh", "-n", str(db_env)], capture_output=True, timeout=10)
        check("zsh can still source db.env", parsed.returncode == 0)
        verify = [c for c in w.http.calls if c[0] == "GET" and "/projects" in c[1]]
        check("the child was verified with the org-scoped projects query",
              bool(verify) and verify[-1][2] == w.http.minted[0] and "org_id=org-dry-dew-75906281" in verify[-1][1])


def test_codex_install_on_stdin_and_retry_after_failure() -> None:
    print("codex: the key reaches Codex on stdin; a failed install is retried")
    with world(roots=("OPENAI_ADMIN_KEY",)) as w:
        w.night()
        first = w.http.minted[0]
        check("only the Studio's row was minted on the Studio", len(w.http.minted) == 1, w.http.minted)
        argv, given = w.codex.calls[0]
        check("Codex is logged in with --with-api-key", argv == ["codex", "login", "--with-api-key"])
        check("the key went on stdin", given == first)
        check("the key never touched argv", all(first not in a for a in argv))
        check("OPENAI_API_KEY was stored too", w.stored("OPENAI_API_KEY") == first)

        w.codex.fail = True
        w.clock.now = T0 + timedelta(days=84)
        receipts = w.night()
        second = w.http.minted[-1]
        entry = w.saved_state()["credentials"]["codex-cli-auth-studio"]
        check("a failed install leaves the install pending", entry["install_pending"] is True, entry)
        check("and does NOT revoke the child Codex still holds", first in w.http.active["openai"])
        check("the new child is stored for the retry", w.stored("OPENAI_API_KEY") == second)
        check("the receipt says the install failed",
              "install:failed_previous_kept_retry_next_night" in outcomes(receipts, "codex-cli-auth-studio"))

        w.codex.fail = False
        w.clock.now = T0 + timedelta(days=85)
        receipts = w.night()
        check("the next night installs from the store", w.codex.calls[-1][1] == second)
        check("then revokes the previous child", first not in w.http.active["openai"])
        check("without minting a third", len(w.http.minted) == 2)
        check("the retry is receipted",
              "install:installed_codex_on_retry" in outcomes(receipts, "codex-cli-auth-studio"))


def test_children_are_minted_only_on_their_own_machine() -> None:
    print("machines: the MacBook row is minted only on the MacBook")
    with world(roots=("OPENAI_ADMIN_KEY", "CLOUDFLARE_TOKEN_ROOT"), machine="macbook") as w:
        receipts = w.night()
        names = {r["credential"] for r in receipts}
        check("the MacBook mints its own Codex row", "codex-cli-auth-macbook" in names)
        check("and never the Studio's rows", not names & {"codex-cli-auth-studio", "cloudflare-deploy-token"}, names)


def test_refused_root_grant_files_the_loop() -> None:
    print("refused root: a 401 on a mint re-files the provider's loop")
    with world(roots=("CLOUDFLARE_TOKEN_ROOT",)) as w:
        w.http.refuse_root = True
        receipts = w.night()
        adds = [a for v, a in w.board.calls if v == "add-loop" and "credential-root-grant:cloudflare" in a["body"]]
        check("one cloudflare loop, saying the grant was refused",
              len(adds) == 1 and "refused" in adds[0]["blocker_detail"], adds)
        check("nothing was stored", w.stored("CLOUDFLARE_API_TOKEN") is None)
        check("the mint failure is receipted with its status",
              "mint:failed_http_401" in outcomes(receipts, "cloudflare-deploy-token"))


# ── values never escape ─────────────────────────────────────────────────────

def test_no_value_reaches_stdout_receipts_or_state() -> None:
    print("values: never printed, never in a receipt, never in state")
    with world(roots=tuple(ROOTS)) as w:
        receipts = w.night()
        w.clock.now = T0 + timedelta(days=85)
        receipts += w.night()
        secrets = list(ROOTS.values()) + w.http.minted
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for line in cm.report_lines(receipts):
                print(line)
        blobs = {"stdout": out.getvalue(), "receipts": w.receipts.read_text(),
                 "state": w.state.read_text()}
        for where, text in blobs.items():
            check(f"no value in {where}", not any(s in text for s in secrets))
        rows = [json.loads(line) for line in w.receipts.read_text().splitlines()]
        check("every receipt carries exactly at/credential/provider/step/outcome",
              all(set(r) == set(cm.RECEIPT_KEYS) for r in rows))
        check("a Child's repr hides its value", "mintfixture" not in repr(cm.Child("id-1", "mintfixture-x")))
        check("the state file is 0600", stat.S_IMODE(w.state.stat().st_mode) == 0o600)


def test_cli_refuses_arguments_and_a_broken_plan() -> None:
    print("cli: exit 64 on arguments, 1 on a refused plan")
    spec = importlib.util.spec_from_file_location("credential_mint_cli", REPO / "ops" / "credential-mint.py")
    assert spec is not None and spec.loader is not None
    cli: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with contextlib.redirect_stderr(io.StringIO()):
        check("arguments are refused", cli.main(["--anything"]) == 64)
    real = cm.load_plan

    def broken(grants_path: Path = cm.GRANTS_PATH, inventory_path: Path = cm.INVENTORY_PATH) -> cm.Plan:
        raise cm.PlanError("fixture disagreement")
    cm.load_plan = broken
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = cli.main([])
        check("a refused plan exits 1 and mints nothing", rc == 1 and "plan refused" in out.getvalue())
    finally:
        cm.load_plan = real


def main() -> int:
    for test in (test_plan_is_consistent_and_refuses_disagreement,
                 test_missing_grant_is_never_a_live_call_and_files_one_loop_each,
                 test_placing_the_grant_closes_its_loop,
                 test_github_app_key_present_files_nothing,
                 test_first_mint_verifies_with_the_new_child_and_stores_it,
                 test_schedule_rotates_only_inside_the_window_and_revokes_the_previous,
                 test_verify_failure_revokes_the_new_child_and_keeps_the_old,
                 test_store_failure_revokes_the_new_child,
                 test_failed_revoke_is_retried_the_next_night,
                 test_neon_child_lands_in_db_env_sourceably,
                 test_codex_install_on_stdin_and_retry_after_failure,
                 test_children_are_minted_only_on_their_own_machine,
                 test_refused_root_grant_files_the_loop,
                 test_no_value_reaches_stdout_receipts_or_state,
                 test_cli_refuses_arguments_and_a_broken_plan):
        test()
    if FAILURES:
        print(f"\ncredential-mint-selftest: {len(FAILURES)} failure(s) of {PASSED + len(FAILURES)}")
        return 1
    print(f"\ncredential-mint-selftest: {PASSED}/{PASSED} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
