#!/usr/bin/env python3
"""Unit proof for the Hermes dispatch desk. No live session, no model spend.

WHAT THIS IS FOR. Joe, 2026-08-20: "i want you to build a bridge with hermes
so that hermes can dispatch to sessions in claude or codex ... hermes will
delegate to the appropriate model for each task i want to do." Hermes is the
router; this is the wire it routes over. Two desk kinds so far:

  claude-session  a LIVE labeled Claude Code session, addressed by its socket
  codex-session   a standing Codex thread, resumed by id so its context carries

THE REFUSAL THAT MAKES IT SAFE TO RUN ON JOE'S MAC. Every ordinary Claude Code
session binds /tmp/cc-socks/<pid>.sock, and Joe's real sessions sit in that
same directory. A pid names a process, not a desk, and a dispatcher that can
address a pid can walk into any window he has open. So a pid socket is refused
as a target — at registration AND again at resolve time, because the file can
be hand-edited between the two. A desk is a session deliberately started with
`claude --messaging-socket-path /tmp/cc-socks/<name>.sock`, which is a
statement of intent that a pid is not.

Run:  python3 tools/room-bridge/test_dispatch_unit.py
Exit 0 = every assertion held.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import dispatch  # noqa: E402

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {e!r}")
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


class Listener:
    """A stand-in desk: binds a labeled socket and records what arrives."""

    def __init__(self, path: str):
        self.path = path
        self.lines: list[str] = []
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(4)
        self._srv.settimeout(2.0)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        # A loop, not one accept: resolve() probes liveness by connecting, and
        # that probe would otherwise eat the only accept the real turn needs.
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn) -> None:
        with conn:
            conn.settimeout(2.0)
            buf = b""
            while True:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self.lines.append(line.decode())
            if buf.strip():
                self.lines.append(buf.decode())

    def close(self) -> None:
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="hermes-dispatch-test-")
    root = Path(tmp.name)
    reg_path = root / "desks.json"
    results = root / "results.jsonl"
    sock_dir = root / "socks"
    sock_dir.mkdir()

    reg = desks.Registry(reg_path)

    # ---- addressing -------------------------------------------------------

    def register_and_resolve():
        live = sock_dir / "claude-desk.sock"
        lis = Listener(str(live))
        try:
            reg.register("claude-desk", "claude-session", socket=str(live))
            got = reg.resolve("claude-desk")
            assert got["kind"] == "claude-session", got
            assert got["socket"] == str(live), got
        finally:
            lis.close()

    check("a labeled desk registers and resolves", register_and_resolve)

    def refuse_pid_at_register():
        try:
            reg.register("sneaky", "claude-session", socket="/tmp/cc-socks/79534.sock")
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("a pid socket was accepted at registration")

    check("a pid socket is refused as a target at registration", refuse_pid_at_register)

    def refuse_pid_at_resolve():
        # hand-edit the file the way a careless script would
        data = json.loads(reg_path.read_text())
        data["desks"]["handmade"] = {
            "kind": "claude-session",
            "socket": "/tmp/cc-socks/12345.sock",
        }
        reg_path.write_text(json.dumps(data))
        try:
            reg.resolve("handmade")
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("a hand-written pid socket resolved")

    check("a pid socket written by hand is refused again at resolve", refuse_pid_at_resolve)

    def refuse_unknown():
        try:
            reg.resolve("no-such-desk")
        except desks.DeskError as e:
            assert e.code == "unknown_desk", e.code
        else:
            raise AssertionError("an unregistered name resolved")

    check("an unregistered name is refused", refuse_unknown)

    def refuse_dead_socket():
        dead = sock_dir / "gone-desk.sock"
        reg.register("gone-desk", "claude-session", socket=str(dead))
        try:
            reg.resolve("gone-desk")
        except desks.DeskError as e:
            assert e.code == "desk_not_live", e.code
        else:
            raise AssertionError("a socket with no listener resolved")

    check("a desk whose session has exited is refused", refuse_dead_socket)

    def refuse_bad_name():
        try:
            reg.register("../etc/passwd", "claude-session", socket=str(sock_dir / "x.sock"))
        except desks.DeskError as e:
            assert e.code == "bad_name", e.code
        else:
            raise AssertionError("a path-shaped name was accepted")

    check("a path-shaped desk name is refused", refuse_bad_name)

    def refuse_unknown_kind():
        try:
            reg.register("weird", "telepathy", socket=str(sock_dir / "x.sock"))
        except desks.DeskError as e:
            assert e.code == "bad_kind", e.code
        else:
            raise AssertionError("an unknown desk kind was accepted")

    check("an unknown desk kind is refused", refuse_unknown_kind)

    def refuse_bad_effort():
        try:
            reg.register(
                "bad-effort", "codex-session", model="gpt-5.1-codex-mini",
                effort="extra-spicy", cwd=str(root),
            )
        except desks.DeskError as e:
            assert e.code == "bad_effort", e.code
        else:
            raise AssertionError("an invalid codex reasoning effort was accepted")

    check("invalid codex reasoning effort is refused", refuse_bad_effort)

    def refuse_claude_effort():
        try:
            reg.register(
                "claude-effort", "claude-session", socket=str(sock_dir / "cl.sock"),
                effort="low",
            )
        except desks.DeskError as e:
            assert e.code == "bad_effort", e.code
        else:
            raise AssertionError("claude-session accepted effort")

    check("claude-session effort is refused", refuse_claude_effort)

    # ---- dispatch to a live Claude session --------------------------------

    def dispatch_to_claude():
        live = sock_dir / "claude-desk.sock"
        lis = Listener(str(live))
        try:
            out = dispatch.dispatch(
                "claude-desk", "count the open loops", registry=reg, results_path=results
            )
            assert out["status"] == "delivered", out
            deadline = __import__("time").monotonic() + 3
            while not lis.lines and __import__("time").monotonic() < deadline:
                __import__("time").sleep(0.05)
            assert lis.lines, "nothing reached the desk socket"
            frame = json.loads(lis.lines[0])
            assert frame["type"] == "user", frame
            assert frame["message"]["content"] == desks.DESK_INSTRUCTION + "\n\ncount the open loops", frame
            assert frame["origin"]["kind"] == "peer", frame
            assert frame["origin"]["from"].startswith("hermes:"), frame
        finally:
            lis.close()

    check("a task reaches a live Claude desk as one peer turn", dispatch_to_claude)

    # ---- dispatch to codex, with a stand-in binary ------------------------

    fake_bin = root / "bin"
    fake_bin.mkdir()
    argv_log = root / "codex-argv.json"
    fake = fake_bin / "codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "argv=sys.argv[1:]\n"
        f"open({str(argv_log)!r},'a').write(json.dumps(argv)+chr(10))\n"
        "resuming = 'resume' in argv[:2]\n"
        "tid = argv[-2] if resuming else 'thread-first-0001'\n"
        "print(json.dumps({'type':'thread.started','thread_id':tid}))\n"
        "out=None\n"
        "for i,a in enumerate(argv):\n"
        "    if a in ('-o','--output-last-message'): out=argv[i+1]\n"
        "if out: open(out,'w').write('the cheap model answered')\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def requires_model_and_effort():
        reg.register("codex-naked", "codex-session", model="gpt-5.1-codex-mini", cwd=str(root))
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        try:
            dispatch.dispatch("codex-naked", "no effort", registry=reg, results_path=results, env=env)
        except desks.DeskError as e:
            assert e.code == "unnamed_model_or_effort", e.code
        else:
            raise AssertionError("a codex desk without effort was dispatched")

    check("codex dispatch without effort is refused", requires_model_and_effort)

    def dispatch_to_codex():
        reg.register("codex-desk", "codex-session", model="gpt-5.1-codex-mini", effort="low",
                     cwd=str(root))
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        out = dispatch.dispatch(
            "codex-desk", "rename the variable", registry=reg,
            results_path=results, env=env,
        )
        assert out["status"] == "completed", out
        assert out["result"] == "the cheap model answered", out
        argv = json.loads(argv_log.read_text().splitlines()[0])
        assert argv[0] == "exec", argv
        assert "-m" in argv and argv[argv.index("-m") + 1] == "gpt-5.1-codex-mini", argv
        assert "-c" in argv and argv[argv.index("-c") + 1] == "model_reasoning_effort=low", argv
        assert "-C" in argv and argv[argv.index("-C") + 1] == str(root), argv
        assert argv[-1] == desks.DESK_INSTRUCTION + "\n\nrename the variable", argv

    check("a task reaches codex headless at the desk's model and directory", dispatch_to_codex)

    def per_task_cwd_starts_fresh_and_keeps_the_desk_thread():
        """flash-run escalation (2026-09-24): Sol works in a throwaway copy per task.
        A per-task cwd must start a fresh thread there and must not re-pin the desk."""
        reg.register("sol-fixer", "codex-session", model="gpt-5.1-codex-mini", effort="low",
                     cwd=str(root), sandbox="workspace-write")
        reg.remember_thread("sol-fixer", "standing-thread")
        task_dir = root / "task-copy"
        task_dir.mkdir(exist_ok=True)
        before = len(argv_log.read_text().splitlines())
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        out = dispatch.dispatch("sol-fixer", "fix it", registry=reg,
                                results_path=root / "sol-fixer-results.ndjson",
                                env=env, cwd=str(task_dir))
        assert out["status"] == "completed", out
        argv = json.loads(argv_log.read_text().splitlines()[before])
        assert "resume" not in argv, argv
        assert argv[argv.index("-C") + 1] == str(task_dir), argv
        assert argv[argv.index("-s") + 1] == "workspace-write", argv
        assert reg.entries()["sol-fixer"].get("thread_id") == "standing-thread"

    check("a per-task cwd starts fresh there and keeps the desk's thread",
          per_task_cwd_starts_fresh_and_keeps_the_desk_thread)

    def codex_out_of_credit_is_its_own_status():
        """Codex prints the limit on stdout and exits 0, so the exit code lies."""
        broke = fake_bin / "codex-broke"
        broke.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "msg = (\"You've hit your usage limit. Visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits "
            "or try again at 11:36 PM.\")\n"
            # the real binary emits BOTH forms; assert against both
            "print('ERROR: ' + msg)\n"
            "print(json.dumps({'type':'error','message':msg}))\n"
            "print(json.dumps({'type':'turn.failed','error':{'message':msg}}))\n"
            "raise SystemExit(0)\n"
        )
        broke.chmod(broke.stat().st_mode | stat.S_IXUSR)
        shim = fake_bin / "codex"
        saved = shim.read_text()
        shim.write_text(broke.read_text())
        try:
            env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
            out = dispatch.dispatch(
                "codex-desk", "anything", registry=reg, results_path=results, env=env,
            )
            assert out["status"] == "quota_exhausted", out
            assert "usage limit" in out["detail"], out
            assert out["retry_after"] == "11:36 PM", out
        finally:
            shim.write_text(saved)
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    check("a seat out of credit reports quota_exhausted, not success",
          codex_out_of_credit_is_its_own_status)

    def codex_keeps_its_own_context():
        """The second task must land in the SAME thread as the first.

        Joe, 2026-08-20: "codex should be able to do the same thing as you. It
        has its own context." A desk that started a new thread every task would
        throw away everything it had been told, which makes it a shot rather
        than a seat.
        """
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        first = json.loads(reg_path.read_text())["desks"]["codex-desk"]["thread_id"]
        assert first == "thread-first-0001", f"first dispatch did not pin a thread: {first}"

        out = dispatch.dispatch(
            "codex-desk", "and now rename the other one", registry=reg,
            results_path=results, env=env,
        )
        assert out["resumed"] is True, out
        assert out["thread_id"] == first, out
        argv = [json.loads(l) for l in argv_log.read_text().splitlines() if l.strip()]
        second = argv[-1]
        assert second[:2] == ["exec", "resume"], second
        assert first in second, f"the thread id was not passed to resume: {second}"
        assert "-c" in second and second[second.index("-c") + 1] == "model_reasoning_effort=low", second
        # `codex exec resume` refuses -C/-s/--add-dir outright ("unexpected
        # argument '-C' found") — caught live 2026-08-22 dispatching a second
        # task through this exact path against the real binary, which the
        # stand-in codex script above never validates. A resumed session
        # already carries the cwd/sandbox/dirs it was FIRST started with.
        assert "-C" not in second, f"resume must never pass -C: {second}"
        assert "-s" not in second, f"resume must never pass -s: {second}"
        assert "--add-dir" not in second, f"resume must never pass --add-dir: {second}"

    check("a Codex desk resumes its own thread, so its context carries",
          codex_keeps_its_own_context)

    def fresh_starts_a_new_thread():
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        out = dispatch.dispatch(
            "codex-desk", "unrelated job", registry=reg, results_path=results,
            env=env, fresh=True,
        )
        assert out["resumed"] is False, out
        argv = [json.loads(l) for l in argv_log.read_text().splitlines() if l.strip()]
        assert argv[-1][:2] != ["exec", "resume"], argv[-1]

    check("--fresh deliberately starts a new Codex thread", fresh_starts_a_new_thread)

    def a_desk_carries_its_own_sandbox_posture():
        """A seat that cannot bind a socket reports the wrong answer confidently.

        Codex investigated this very bridge and returned a negative — every
        probe "Operation not permitted" — which was its own workspace-write
        sandbox, not the machine. The tell was that it could not even take the
        git index lock to commit on its own branch. A desk that needs to bind
        or write outside its workspace has to say so at registration, or the
        seat keeps reporting its cage as a fact about the world.
        """
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        reg.register("codex-wide", "codex-session", model="m", cwd=str(root),
                     effort="medium",
                     sandbox="workspace-write", add_dirs=[str(root / "probe")])
        dispatch.dispatch("codex-wide", "bind something", registry=reg,
                          results_path=results, env=env)
        argv = [json.loads(l) for l in argv_log.read_text().splitlines() if l.strip()][-1]
        assert "-s" in argv and argv[argv.index("-s") + 1] == "workspace-write", argv
        assert "--add-dir" in argv, argv
        assert argv[argv.index("--add-dir") + 1] == str(root / "probe"), argv

    check("a desk carries the sandbox posture its work needs",
          a_desk_carries_its_own_sandbox_posture)

    def default_desks_stay_locked_down():
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        dispatch.dispatch("codex-desk", "ordinary work", registry=reg,
                          results_path=results, env=env)
        argv = [json.loads(l) for l in argv_log.read_text().splitlines() if l.strip()][-1]
        assert "--add-dir" not in argv, argv
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv, argv
        assert "sandbox_workspace_write.network_access=true" not in argv, argv
        assert not any("network_proxy" in value for value in argv), argv

    check("a desk that asked for nothing extra stays as locked down as before",
          default_desks_stay_locked_down)

    def a_call_scoped_codex_posture_is_exact_and_not_registered():
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        overrides = (
            "sandbox_workspace_write.network_access=true",
            'features.network_proxy={enabled=true,domains={"github.com"="allow","api.github.com"="allow"}}',
        )
        dispatch.dispatch(
            "codex-desk", "deliver one reviewed branch", registry=reg,
            results_path=results, env=env, fresh=True,
            config_overrides=overrides,
        )
        argv = [json.loads(l) for l in argv_log.read_text().splitlines() if l.strip()][-1]
        pairs = [argv[i:i + 2] for i in range(len(argv) - 1)]
        for value in overrides:
            assert ["-c", value] in pairs, argv
        assert "config_overrides" not in reg.resolve("codex-desk"), reg.resolve("codex-desk")

    check("a reviewed call-scoped Codex posture is exact and not registry authority",
          a_call_scoped_codex_posture_is_exact_and_not_registered)

    def dispatched_codex_fires_its_hooks():
        """A dispatched seat must behave like a hand-run one.

        Codex runs enabled hooks only where their trust is already persisted,
        so an invocation without --dangerously-bypass-hook-trust quietly skips
        hooks a person running the same command would fire. The repo already
        requires the flag at every other call site and fails any new one that
        omits it; this dispatcher was a new one that omitted it.
        """
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        dispatch.dispatch("codex-desk", "hook check", registry=reg,
                          results_path=results, env=env)
        argv = [json.loads(l) for l in argv_log.read_text().splitlines() if l.strip()][-1]
        assert "--dangerously-bypass-hook-trust" in argv, argv

    check("a dispatched Codex run fires its hooks like a hand-run one",
          dispatched_codex_fires_its_hooks)

    # ---- the trail Hermes reads back --------------------------------------

    def results_are_ndjson():
        lines = [l for l in results.read_text().splitlines() if l.strip()]
        assert len(lines) == 9, f"expected one line per dispatch, got {len(lines)}"
        rows = [json.loads(l) for l in lines]
        assert rows[0]["desk"] == "claude-desk", rows[0]
        assert rows[0]["kind"] == "claude-session", rows[0]
        assert rows[1]["desk"] == "codex-desk", rows[1]
        assert rows[2]["status"] == "quota_exhausted", rows[2]
        assert rows[3]["resumed"] is True, rows[3]
        for r in rows:
            assert r["task"], r
            assert r["status"], r
            assert r["dispatched_at"], r

    check("every dispatch leaves one NDJSON line for Hermes to read", results_are_ndjson)

    def refuses_pid_socket_through_dispatch():
        data = json.loads(reg_path.read_text())
        data["desks"]["backdoor"] = {"kind": "claude-session", "socket": "/tmp/cc-socks/1.sock"}
        reg_path.write_text(json.dumps(data))
        try:
            dispatch.dispatch("backdoor", "hi", registry=reg, results_path=results)
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("dispatch delivered to a pid socket")

    check("dispatch itself will not deliver to a pid socket", refuses_pid_socket_through_dispatch)

    # ---------------------------------------------------------------- WR-000119
    # The dispatch spine's two call sites, each proved to write only what it
    # observed first-hand. No record layer is reached: the verb caller is
    # injected, so what is under test is WHAT each site sends, which is the
    # whole of the first-hand discipline.

    def bridge_mints_the_link_with_the_turn_it_just_wrote():
        import bridge  # noqa: PLC0415 — imported here so a bridge import error
        sent = {}

        def fake_call(verb, args, **kwargs):
            sent["verb"] = verb
            sent["args"] = args
            sent["kwargs"] = kwargs
            return {"ok": True, "dispatch_ref": args["dispatch_ref"]}

        bridge.record_dispatch_link(
            turn_msg_id="11111111-1111-4111-8111-111111111111",
            session_id="claude-desktop-7",
            work_request_id="22222222-2222-4222-8222-222222222222",
            dispatch_ref="33333333-3333-4333-8333-333333333333",
            call_verb=fake_call,
        )
        assert sent["verb"] == "record-dispatch-link", sent["verb"]
        # The three facts the bridge holds at that moment, and no fourth.
        assert sent["args"]["turn_msg_id"] == "11111111-1111-4111-8111-111111111111"
        assert sent["args"]["session_id"] == "claude-desktop-7"
        assert sent["args"]["work_request_id"] == "22222222-2222-4222-8222-222222222222"
        # NO ACTOR ARGUMENT ANYWHERE. The identity is derived by the Worker.
        assert "actor" not in sent["args"] and "by_actor" not in sent["args"], sent["args"]
        # hermes-pilot is reached by the profile selector, not by naming it.
        assert sent["kwargs"].get("client_profile") == "hermes-projector", sent["kwargs"]

    check("the bridge mints the link with the turn msg_id it just wrote",
          bridge_mints_the_link_with_the_turn_it_just_wrote)

    def desk_acknowledges_received_and_can_never_send_acknowledged():
        sent = {}

        def fake_call(verb, args, **kwargs):
            sent["verb"] = verb
            sent["args"] = args
            return {"ok": True}

        dispatch.acknowledge_received(
            "33333333-3333-4333-8333-333333333333",
            desk="claude-desk", log_offset=4096,
            injected_at="2026-09-19T00:00:00Z", call_verb=fake_call,
        )
        assert sent["verb"] == "acknowledge-dispatch", sent["verb"]
        assert sent["args"]["stage"] == "received", sent["args"]
        # The evidence is MEASURED -- the desk name and the byte offset the turn
        # landed at -- and never "it probably arrived".
        assert "claude-desk" in sent["args"]["evidence"], sent["args"]
        assert "4096" in sent["args"]["evidence"], sent["args"]
        assert "actor" not in sent["args"], sent["args"]
        # THE STAGE IS NOT A PARAMETER. There is no argument that could carry
        # `acknowledged` into this call site at all.
        assert dispatch.DESK_ACK_STAGE == "received", dispatch.DESK_ACK_STAGE
        import inspect  # noqa: PLC0415
        params = inspect.signature(dispatch.acknowledge_received).parameters
        assert "stage" not in params, sorted(params)

    check("the desk sends stage received and has no way to send acknowledged",
          desk_acknowledges_received_and_can_never_send_acknowledged)

    def an_acknowledgement_names_the_dispatch_it_acknowledges():
        try:
            dispatch.acknowledge_received("", desk="claude-desk", log_offset=0,
                                          call_verb=lambda *a, **k: {"ok": True})
        except desks.DeskError as e:
            assert e.code == "dispatch_ref_missing", e.code
        else:
            raise AssertionError("an ack with no dispatch_ref was sent")

    check("an acknowledgement with no dispatch_ref is refused before it is sent",
          an_acknowledgement_names_the_dispatch_it_acknowledges)

    tmp.cleanup()
    print()
    if FAILURES:
        print(f"hermes-dispatch unit: {len(FAILURES)} FAILED")
        return 1
    print("hermes-dispatch unit: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
