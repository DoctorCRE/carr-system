#!/usr/bin/env python3
"""flash-run.py — run one coding task on the local Flash-Next model, supervised by Jev.

WHY THIS EXISTS. The 2026-09-23 stress test made Qwen3.8-Flash-Next the everyday
coding workhorse (decision ab0dc622) and the Jev experiment that followed found
what makes it reliable (decision cb56f652): several low-effort attempts, the
tests run against each, and ONE Jev choice over the candidates with that
evidence in named state fields. With evidence the raw best-of-3 went 16/16.
The generic 0.6 confidence gate was wrong for it: it fell back to attempt 1 and
lost. This file is that finding turned into the command Joe actually runs.

THE FLOW for `flash-run "<task>" --test "<command>"`:
  1. intake (ops/jev_intake.py): the ambiguity stop (#3), the escalation router
     (#5), the effort picker (#2), the context picker (#1), the worked-example
     picker (#23), and the mistake notebook's recall (#15).
  2. up to N attempts (default 3 with a test command, else 1), each in its own
     copy of the working tree, by the `flash` launcher. The in-session checks ride
     along through hooks/jev-supervisor.py in advise mode (~/.claude-local
     settings). SPEED TUNING (2026-09-24, measured on the 16-task scorecard):
     the first attempt of low-effort work runs without model thinking; the run
     stops at the first attempt whose tests pass (--all-attempts for full
     best-of-N); each retry gets the previous attempt's failing test output;
     and an attempt is cut off at ten minutes.
  3. the tests run in every copy; ops/jev_best_of.py picks one candidate or
     "none". A single passing candidate is taken without asking Jev.
  4. the chosen patch is applied to the real tree and the tests run again there.
     Review triage (#17) scores the risk of the change.
  5. on failure: the mistake is written to the notebook and a handoff pack (#10)
     is written; --escalate auto sends it through the Model Room (2026-09-24,
     Flash replaces Sonnet for scoped coding): a failed task to the Sol fixer desk,
     whose change is applied only if the test passes; a task routed away as a
     design/judgment call to the Opus desk. Never a direct model call.

Every run appends one row to out/flash-runs.jsonl, the real-use record the week
of tracking reads (`flash-run stats`).

OTHER SUBCOMMANDS
  flash-run plan <file>        route each step of a plan local/escalate (#18)
  flash-run scorecard          the standing model scorecard (#25)
  flash-run stats              summarize out/flash-runs.jsonl
  flash-run note "<what>" --fix "<fix>"   add to the mistake notebook by hand

Exit codes: 0 applied and tests pass (or no test given and an attempt ran),
3 the task is ambiguous, 4 routed or escalated away from the local model,
5 no candidate was good enough, 2 usage or environment problem.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import flashlib
OUT = os.path.join(REPO, "out")
RUNS_LOG = os.path.join(OUT, "flash-runs.jsonl")
EXAMPLES_LOG = os.path.join(OUT, "flash-examples.jsonl")
HANDOFF_DIR = os.path.join(OUT, "flash-handoffs")
FLASH = os.environ.get("FLASH_BIN") or shutil.which("flash") or os.path.expanduser("~/.local/bin/flash")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "out", "dist",
             "build"}
# Runaway cutoff. A scoped slice that has not finished in ten minutes is thinking in circles
# (the run-length benchmark task spent 214 s on its first try and still failed), and the
# next attempt, which starts with that attempt's failure in its prompt, is the better bet.
ATTEMPT_TIMEOUT = 600
ATTEMPT_TOOLS = ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
TEST_TIMEOUT = 600
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def flash_port(url=None):
    """The loopback port the Flash server listens on, or None when the Flash URL is not loopback.

    Only the Flash-specific setting counts (CARR_FLASH_URL, else the launcher's own 127.0.0.1:8000). The ambient
    ANTHROPIC_BASE_URL belongs to whoever CALLS flash-run (a Claude session carries https://api.anthropic.com), and
    the launcher overwrites it anyway; reading it closed the Flash port for the first live run (2026-09-27)."""
    import urllib.parse
    u = urllib.parse.urlparse(url or os.environ.get("CARR_FLASH_URL") or "http://127.0.0.1:8000")
    return (u.port or 80) if u.hostname in ("127.0.0.1", "localhost") else None


def scratch_for(work):
    """The sandboxed run's HOME and TMPDIR live here: a sibling of the tree, never inside it, so what the launcher
    (its CLAUDE_CONFIG_DIR session logs, caches) and the tests (pytest tmp_path) write there can never reach the
    patch, the committed branch or the diff posted to the room (third review of #1324). Writable to the sandbox,
    removed with the run."""
    return os.path.realpath(work).rstrip(os.sep) + ".scratch"


def sandbox_profile(work, *, reads=(), execs=(), port=None):
    """A macOS seatbelt profile for a model-driven run (the Flash agent and the tests it writes), the same shape as
    tools/flash-script.py sandbox_profile() (#1250/#1316). The task text is untrusted, so a planted line can steer
    the agent toward secrets or the network. Writes go only inside `work` (the throwaway attempt copy or worktree);
    nothing under the home folder, the shared temp dirs, the Postgres/Homebrew state, /etc/ssh or the keychains is
    readable except `work`, the interpreters, and the `reads` passed (the project's .venv/node_modules and the Flash
    launcher). Network is closed except the loopback Flash port. The only programs that may start are system tool
    dirs and the `execs` passed, so a binary the model writes into `work` can never be executed. mach-lookup,
    Apple Events and signals to other processes are denied, which closes `open`, the keychain agent and launchd."""
    work = os.path.realpath(work)
    scratch = scratch_for(work)
    home = os.path.realpath(os.path.expanduser("~"))
    interp_prefixes = {os.path.realpath(p) for p in (sys.prefix, sys.base_prefix)}
    reads = sorted({work, scratch, *interp_prefixes, *(os.path.realpath(p) for p in reads if p)})
    exec_dirs = sorted({"/usr/bin", "/bin", "/usr/libexec", "/opt/homebrew", "/usr/local/bin",
                        *(os.path.realpath(p) for p in execs if p)})
    # The running interpreter may live under an allowed dir already, but a virtualenv often copies python outside
    # one; allow it explicitly (its own path and resolved path), the way flash-script does.
    interp_execs = sorted({sys.executable, os.path.realpath(sys.executable)})
    private = sorted({os.path.realpath(p) for p in (
        home, "/private/tmp", "/private/var/folders", "/Users/Shared", "/opt/homebrew/var", "/opt/homebrew/etc",
        "/usr/local/var", "/usr/local/etc", "/etc/ssh", "/Library/Keychains")})
    # Node needs only openssl.cnf, including its resolved file target when symlinked.
    # Keep this literal exception separate from recursive reads: private/certs stay denied.
    openssl = sorted({path for p in ("/opt/homebrew/etc/openssl@3/openssl.cnf",
                                    "/usr/local/etc/openssl@3/openssl.cnf")
                      if os.path.isfile(p) for path in (p, os.path.realpath(p))})
    openssl_read = ("(allow file-read* " + " ".join(f'(literal "{p}")' for p in openssl) + ")"
                    if openssl else "")
    allpaths = [*private, *reads, *openssl, *exec_dirs, *interp_execs, scratch]
    if any('"' in p or "\\" in p for p in allpaths):
        raise ValueError("path not expressible in a sandbox profile")
    net = f'(allow network-outbound (remote ip "localhost:{port}"))' if port else ""
    return ("(version 1)(allow default)"
            f"(deny network*){net}"
            "(deny file-read* " + " ".join(f'(subpath "{p}")' for p in private) + ")"
            "(allow file-read* " + " ".join(f'(subpath "{p}")' for p in reads) + ")"
            + openssl_read +
            "(allow file-read-metadata)"
            "(deny file-write*)"
            f'(allow file-write* (subpath "{work}") (subpath "{scratch}") (literal "/dev/null"))'
            # Git metadata is never writable from inside, anywhere: a `.git` file or directory, at any depth. Model
            # code that planted core.fsmonitor or a filter in a .git/config (or swapped a worktree's .git pointer)
            # would have it run by the UNSANDBOXED git that reads the patch back afterwards (re-review of #1324).
            # This rule comes after the allow above, so it wins.
            '(deny file-write* (regex #"/\\.git(/|$)"))'
            "(deny process-exec*)"
            "(allow process-exec " + " ".join(f'(subpath "{p}")' for p in exec_dirs)
            + " " + " ".join(f'(literal "{p}")' for p in interp_execs) + ")"
            "(deny mach-lookup)(deny appleevent-send)(deny signal (target others))")


def sandbox_wrap(argv, work, *, reads=(), execs=(), port=None):
    """Prefix argv with sandbox-exec under a profile for `work`. Raises FileNotFoundError when sandbox-exec is
    absent, so the caller fails closed rather than running model code unsandboxed."""
    if not os.path.exists(SANDBOX_EXEC):
        raise FileNotFoundError("sandbox-exec is not on this machine")
    return [SANDBOX_EXEC, "-p", sandbox_profile(work, reads=reads, execs=execs, port=port), *argv]


def _lib(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _now():
    return datetime.now(timezone.utc).isoformat()


def _append(path, row):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def _say(msg):
    print(f"flash-run: {msg}", flush=True)


def _sh(cmd, cwd, timeout, env=None):
    try:
        done = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True,
                              text=True, timeout=timeout, env=env)
        return done.returncode, (done.stdout + done.stderr)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"timed out after {timeout}s\n{partial if isinstance(partial, str) else ''}"


# Every process a contained run starts carries this marker in its environment, so a descendant that setsid()'d AND
# was reparented to init (its parent already gone) can still be found and killed.
RUN_MARK = "CARR_FLASH_CONTAIN"


def _children_map():
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    kids = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    return kids


def _descendants(root):
    """Every live descendant of root, walked by ppid. A setsid() child leaves the process GROUP but keeps its
    parent, so it is found here as long as the tree is still attached (we walk before killing anything)."""
    if not root:
        return set()
    kids, seen, stack = _children_map(), set(), [root]
    while stack:
        for child in kids.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _marked(token):
    """Processes whose environment carries this run's marker, including orphans already reparented to init."""
    needle, pids = f"{RUN_MARK}={token}", set()
    if os.path.isdir("/proc"):  # Linux
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open(f"/proc/{d}/environ", "rb") as fh:
                        if needle.encode() in fh.read().split(b"\0"):
                            pids.add(int(d))
                except OSError:
                    pass
    else:  # macOS: ps -E appends each own-user process's environment
        try:
            out = subprocess.run(["ps", "-A", "-E", "-ww", "-o", "pid=,command="], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return pids
        for line in out.splitlines():
            pid, _, rest = line.strip().partition(" ")
            if pid.isdigit() and needle in rest.split():
                pids.add(int(pid))
    pids.discard(os.getpid())
    return pids


def kill_run(root, token):
    """Stop, then SIGKILL, every process of a contained run: root's live descendant tree and anything carrying the
    run's marker. SIGSTOP first, repeated until no new process appears, so nothing can fork a replacement between
    the snapshot and the kill; then SIGKILL all of them and root's process group."""
    stopped = set()
    for _ in range(8):
        found = _descendants(root) | _marked(token)
        if root:
            found.add(root)
        new = found - stopped
        if not new:
            break
        for pid in new:
            try:
                os.kill(pid, signal.SIGSTOP)
            except OSError:
                pass
        stopped |= new
    for pid in stopped:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if root:
        try:
            os.killpg(root, signal.SIGKILL)
        except OSError:
            pass


def run_contained(argv, cwd, timeout, env=None):
    """Run argv (a list, no shell) contained: its own session, output to a file outside `cwd` (never in the tree
    git later reads), a per-run marker in its environment, and a timeout that bounds the wall clock. On timeout
    the WHOLE run is killed (kill_run), including a setsid() descendant, and we never block on an inherited pipe.
    On a normal exit, anything the run left behind (a daemonised child) is killed too. Returns (exit_code, output),
    exit_code None on timeout."""
    token = uuid.uuid4().hex
    child_env = dict(os.environ if env is None else env)
    child_env[RUN_MARK] = token
    fd, out_path = tempfile.mkstemp(prefix="flash-contained-", suffix=".log")
    try:
        with os.fdopen(fd, "wb") as fh:
            try:
                p = subprocess.Popen(argv, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=child_env, start_new_session=True)
            except OSError as exc:
                return 127, f"could not start {argv[0]}: {type(exc).__name__}"
            try:
                p.wait(timeout=max(1.0, timeout))
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
            kill_run(p.pid if timed_out else None, token)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        try:
            with open(out_path, "r", errors="replace") as fh:
                output = fh.read()
        except OSError:
            output = ""
        return (None if timed_out else p.returncode), output
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def bounded_run(argv, cwd, timeout, env=None):
    """run_contained with the shell convention: (124, "timed out ...") on timeout."""
    code, output = run_contained(argv, cwd, timeout, env=env)
    if code is None:
        return 124, f"timed out after {timeout:.0f}s\n{output}"
    return code, output


def _run_test(test_cmd, cwd, timeout, *, sandbox=False, reads=(), execs=(), port=None):
    """Run the test command in `cwd`. Sandboxed, it goes through /bin/sh under the seatbelt profile (model-written
    tests are untrusted); unsandboxed it keeps the old shell run. Either way the timeout bounds the wall clock."""
    if sandbox:
        argv = sandbox_wrap(["/bin/sh", "-c", test_cmd], cwd, reads=reads, execs=execs, port=port)
        env = _sandbox_env(os.environ, cwd)
        try:
            return bounded_run(argv, cwd, timeout, env=env)
        finally:
            drop_scratch(cwd)  # beside a real project on an interactive run, so never left behind
    return _sh(test_cmd, cwd, timeout)


def _tracked_files(cwd):
    code, out = _sh(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd, 60)
    if code == 0 and out:
        return [p for p in out.split("\0") if p and os.path.isfile(os.path.join(cwd, p))]
    files = []
    for base, dirs, names in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            files.append(os.path.relpath(os.path.join(base, name), cwd))
    return files


# Git run by THIS process after model code touched a tree must not trust anything in that tree (re-review of #1324):
# the repository lives in a git dir the model can never write (gitdir_for: a sibling of the copy, outside the
# sandbox's writable folder), no system or global config is read, and the exec-capable hooks are pinned off. An
# in-tree .gitattributes can only name drivers, and no trusted config defines any, so it cannot run anything.
GIT_HARDEN = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.untrackedCache=false"]


_GIT_ENV_LIB = None


def git_env(base=None, excludes=None):
    """A git environment that trusts nothing inherited: ops/git_env.scrubbed_env drops the location variables
    (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...) and every GIT_CONFIG_COUNT/KEY/VALUE pair (which can carry
    core.worktree); system and global config are off. The only config added back is our own excludes file."""
    global _GIT_ENV_LIB
    if _GIT_ENV_LIB is None:
        _GIT_ENV_LIB = _lib("git_env")
    env = _GIT_ENV_LIB.scrubbed_env(base)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    if excludes:
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0=excludes)
    return env


def gitdir_for(dest):
    """The trusted git dir of a throwaway copy: a sibling of it, so never inside the folder model code may write."""
    return os.path.realpath(dest).rstrip(os.sep) + ".gitdir"


def tgit(gitdir, worktree, *args, timeout=120, env=None, excludes=None):
    """git on `worktree` through an explicit, trusted git dir, hardened; never discovers a .git in the tree."""
    return _sh(["git", "--git-dir", gitdir, "--work-tree", worktree, *GIT_HARDEN, *args], worktree, timeout,
               env=git_env(env, excludes))


def make_copy(cwd, dest):
    """A throwaway copy of the working tree with a baseline commit, so the attempt's
    change can be read back as a patch no matter what state the real tree is in. The copy's
    repository is kept OUTSIDE it (gitdir_for), so nothing written into the copy is ever git metadata."""
    for rel in _tracked_files(cwd):
        src = os.path.join(cwd, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    # The project's installed dependencies are linked, not copied: .venv for Python, node_modules so a
    # `node --test` in the copy resolves the project's packages (the Model Room queue's code tasks use both).
    for dep in (".venv", "node_modules"):
        src = os.path.join(cwd, dep)
        if os.path.isdir(src) and not os.path.lexists(os.path.join(dest, dep)):
            os.symlink(src, os.path.join(dest, dep))
    gitdir = gitdir_for(dest)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=flash@local", "-c", "user.name=flash",
                  "commit", "-q", "--no-verify", "-m", "baseline"]):
        tgit(gitdir, dest, *args)


def read_patch(dest):
    # Leave out what running code generates (bytecode caches, build output): the copy never took those
    # folders, so a change to them re-creates files the real folder already has and the patch won't apply.
    skip = [f":(exclude,glob)**/{d}/**" for d in sorted(SKIP_DIRS - {".git"})] + [":(exclude,glob)**/*.pyc"]
    gitdir = gitdir_for(dest)
    tgit(gitdir, dest, "add", "-A", "--", ".", *skip)
    _, patch = tgit(gitdir, dest, "diff", "--cached", "--binary")
    return patch


def build_prompt(task, test_cmd, context_files, recalled, example):
    parts = [task.strip()]
    if context_files:
        parts.append("Files most likely involved: " + ", ".join(context_files))
    if example:
        parts.append("A similar task solved before:\n" + example)
    if recalled:
        parts.append("Mistakes made on similar tasks before — avoid them:\n" + "\n".join(recalled))
    if test_cmd:
        parts.append(f"Verify with: {test_cmd}\nRun it, fix until it passes, then stop. "
                     "Keep the change small; do not edit the tests to make them pass.")
    else:
        parts.append("Keep the change small and focused on the task.")
    return "\n\n".join(parts)


def retry_prompt(prompt, previous):
    """The next attempt's prompt: the task plus what the last attempt's tests said.

    A blind retry spends a full attempt to find the same mistake. Handing over the
    failing output makes the retry a fix, which is how a person uses a red test.
    """
    if not previous or previous.get("test_exit_code") in (None, 0):
        return prompt
    return (prompt + "\n\nA previous attempt at this task failed its tests with this output. "
            "Avoid the same mistake:\n" + (previous.get("test_output") or "")[-2500:])


def think_for(mode, effort, attempt):
    """Whether this attempt runs with model thinking.

    auto: the first attempt of low-effort work runs without thinking, which is where the time
    went (a few thousand thinking tokens at ~70/s before any code). A retry, or anything
    the effort picker rated above low, thinks: a failure is evidence the problem needs it.
    """
    if mode == "on":
        return True
    if mode == "off":
        return False
    return not (effort == "low" and attempt == 1)


MAX_TASK_RULES = 5
RULE_STATEMENT_CHARS = 600
# Said to the rule picker, not to Flash. Measured 2026-09-24: describing the task only as
# "coding in carr-system" made Jev pick session rules (worktree-per-session, own the merge)
# for a disposable copy that must not touch git; naming the real boundary drops them, and
# a gate-building task still gets write-the-test-first.
RULE_SITUATION = ("A local coding model (Flash) is about to write code for ONE scoped task inside a "
                  "disposable copy of the files. It does no git, branching, worktrees, pushes, "
                  "merges or delivery (the flash-run harness owns all of that, and tests and "
                  "applies the patch). It only reads, edits and runs tests. The task: ")


def pick_rules(task, situation=RULE_SITUATION, **judge):
    """Return authoritative rules and a visible note for pending semantic advice.

    Flash has no boot load or enforcing hooks, so the selector considers the whole
    active corpus. Injected judgment arguments use the same bounded batch interface.
    """
    try:
        selector = _lib("rule_trigger_delivery")
        corpus = selector.load_rules()
        picked, report = selector.judge_budgeted(situation + task, corpus, [], **judge)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"[:300]
    by_id = {rule["id"]: rule for rule in corpus}
    rules = [{**by_id[row["id"]], **row}
             for row in sorted(picked.values(), key=lambda r: (-r["probability"], r["id"]))]
    gaps = [f"{key}={report[key]}" for key, healthy in
            (("rank_status", ("ok", "not_needed", "deterministic_shortlist")),
             ("bind_status", ("judged", "none")))
            if report.get(key) not in healthy]
    if report.get("deadline_hit"):
        gaps.append(f"deadline hit, unjudged={report.get('unjudged', [])}")
    if report.get("review_required"):
        candidates = ", ".join(sorted(report.get("advisory_candidates") or {}))
        gaps.append(f"review required for rule suggestions: {candidates}")
    note = ("rule judgment degraded: " + "; ".join(gaps))[:300] if gaps else None
    return rules[:MAX_TASK_RULES], note


def rules_block(rules):
    """The system-prompt addition for the picked rules, or None when nothing binds."""
    if not rules:
        return None
    lines = ["RULES FOR THIS TASK (picked from the practice's taught rules for this task alone; "
             "follow them):"]
    for rule in rules[:MAX_TASK_RULES]:
        text = (rule.get("statement") or rule.get("gist") or "").strip()
        if len(text) > RULE_STATEMENT_CHARS:
            text = text[:RULE_STATEMENT_CHARS].rsplit(" ", 1)[0] + " ..."
        lines.append(f"- [{rule.get('id')}] {rule.get('gist', '').strip()}: {text}")
    return "\n".join(lines)


def _dep_reads(cwd):
    """The dependency dirs a sandboxed attempt may read (resolved): the project's virtualenv and node_modules, plus
    the Flash launcher. Everything else under the home folder stays denied."""
    reads = [FLASH]
    for dep in (".venv", "node_modules"):
        p = os.path.join(cwd, dep)
        if os.path.exists(p):
            reads.append(os.path.realpath(p))
    return reads


def agent_execs():
    """The programs the sandboxed AGENT (not its tests) may start beyond the system dirs: the Flash launcher itself,
    by exact path. Live run 2026-09-27: without it sandbox-exec's own execvp of the launcher is refused (exit 71)."""
    return [FLASH]


def _sandbox_env(base, dest):
    """Env for a sandboxed run: a throwaway HOME and TMPDIR in the scratch folder BESIDE the tree (scratch_for), so
    the Flash launcher's $HOME/.claude-local, caches and test temp files land where writes are allowed but never in
    the patch; and no inherited credential beyond a minimal allowlist. The launcher re-exports the Anthropic base
    URL and model itself. The caller removes the scratch folder (drop_scratch)."""
    scratch = scratch_for(dest)
    home, tmp = os.path.join(scratch, "home"), os.path.join(scratch, "tmp")
    os.makedirs(home, exist_ok=True)
    os.makedirs(tmp, exist_ok=True)
    # No ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY from the caller: the launcher sets its own local values, and the
    # caller's may be a real API key that model-driven code could read from its environment.
    keep = {k: base[k] for k in ("CARR_FLASH_URL", "CARR_FLASH_MODEL",
                                 "MAX_THINKING_TOKENS", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
            if k in base}
    # CLAUDE_CODE_TMPDIR: the Claude Code harness ignores TMPDIR and opens /tmp/claude-<uid> (live run 2026-09-27:
    # EPERM on /tmp/claude-501), a folder SHARED with every other Claude session on the host; point it at scratch.
    return {"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "HOME": home, "TMPDIR": tmp, "CLAUDE_CODE_TMPDIR": tmp,
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", **keep}


def drop_scratch(dest):
    shutil.rmtree(scratch_for(dest), ignore_errors=True)


def run_attempt(n, cwd, prompt, test_cmd, effort, workdir, think=True, rules_text=None, sandbox=False):
    dest = os.path.join(workdir, f"attempt-{n}")
    os.makedirs(dest)
    make_copy(cwd, dest)
    allowed = ["Read", "Edit", "Write", "Glob", "Grep",
               "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)", "Bash(rg:*)", "Bash(git diff:*)",
               "Bash(git status:*)", "Bash(python3:*)", "Bash(node:*)"]
    if test_cmd:
        allowed.append(f"Bash({test_cmd})")
    started = time.monotonic()
    # MAX_THINKING_TOKENS=0 makes the harness send no thinking budget, which the ds4 server
    # serves in non-thinking mode (verified 2026-09-24 from its log: no THINKING marker).
    env = None if think else dict(os.environ, MAX_THINKING_TOKENS="0")
    # --tools limits the tool DEFINITIONS sent, not just permissions: measured 2026-09-24 the
    # start-up prompt drops 14,863 -> 4,320 tokens (cold read 14.4 s -> 4.3 s). A scoped fix
    # needs nothing beyond these six; the interactive `flash` command keeps the full set.
    # Appended, not replacing: the base prompt stays byte-identical across tasks so the
    # server's prompt cache still covers it; only the rules tail is read fresh.
    extra = ["--append-system-prompt", rules_text] if rules_text else []
    argv = [FLASH, "-p", prompt, "--effort", effort or "low", "--permission-mode", "acceptEdits",
            "--tools", *ATTEMPT_TOOLS, *extra, "--allowedTools", *allowed]
    reads, execs, port = _dep_reads(cwd), [os.path.join(cwd, ".venv", "bin")], flash_port()
    gitdir = gitdir_for(dest)
    try:
        with flashlib.request_scope(os.environ.get("CARR_FLASH_URL", flashlib.LOCAL_URL)):
            if sandbox:
                execs = [*execs, *agent_execs()]
                # The agent runs model-driven code, so it is sandboxed: writes only inside this attempt copy (never git
                # metadata), no reads under home except its deps and its read-only git dir, network only to the local Flash
                # port. A throwaway HOME keeps its config writable. It runs contained, so nothing it starts outlives it.
                argv = sandbox_wrap(argv, dest, reads=[*reads, gitdir], execs=execs, port=port)
                env = dict(_sandbox_env(env or os.environ, dest), GIT_DIR=gitdir, GIT_WORK_TREE=dest)
                code, transcript = bounded_run(argv, dest, ATTEMPT_TIMEOUT, env=env)
            else:
                env = dict(env or os.environ, GIT_DIR=gitdir, GIT_WORK_TREE=dest)
                code, transcript = _sh(argv, dest, ATTEMPT_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, transcript = 1, f"Flash unavailable: {exc}"
    elapsed = round(time.monotonic() - started, 1)
    test_code, test_out = (None, "")
    if test_cmd:
        test_code, test_out = _run_test(test_cmd, dest, TEST_TIMEOUT, sandbox=sandbox, reads=reads,
                                        execs=execs, port=port)
    patch = read_patch(dest)
    return {"id": f"attempt-{n}", "code_or_diff": patch[:20000], "patch": patch,
            "test_output": test_out[-6000:], "test_exit_code": test_code,
            "probe_results": {"agent_exit_code": code, "patch_lines": patch.count("\n"),
                              "tests_passed": test_code == 0 if test_cmd else None},
            "agent_output": transcript[-3000:], "elapsed_s": elapsed}


def apply_patch(cwd, patch):
    if not patch.strip():
        return False, "empty patch"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
        fh.write(patch)
        name = fh.name
    try:
        code, out = _sh(["git", "apply", "--whitespace=nowarn", name], cwd, 120)
        return code == 0, out
    finally:
        os.unlink(name)


# Joe's decision 2026-09-24: Flash replaces Sonnet for scoped, testable coding, and what it
# cannot do goes to Sol or Opus through the Model Room (decision 284028a5: no direct model
# calls). A failed attempt is a code problem -> the Sol fixer desk (writable, no room seat),
# which edits a throwaway copy of the task folder; flash-run reads that back as a patch,
# applies it to the real folder and keeps it only if the test passes. A task routed away
# before any attempt is a design/judgment call -> the Opus desk, in the background.
# Why not the Sol room seat (codex-desk): it is read-only on purpose, because it answers
# partner-room turns inside the canonical checkout, which must stay clean.
# The desks come from the Model Room routing policy (ops/config/model-routes.v1.json, read through
# ops/jev_model_route.desk_for), so this hand-off and the router always name the same desks.
ESCALATION_DESKS = {kind: _lib("jev_model_route").desk_for(kind) for kind in ("code", "judgment")}
ESCALATION_FILE_CHARS = 20000
DIFF_BLOCK = re.compile(r"```(?:diff|patch)\s*\n(.*?)```", re.S)


def extract_diff(answer):
    match = DIFF_BLOCK.search(answer or "")
    return match.group(1) if match else None


def _load_path(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _room_dispatch(desk, text, cwd=None):
    room = _load_path(os.path.join(REPO, "tools", "room-bridge", "dispatch.py"), "room_dispatch")
    return room.dispatch(desk, text, cwd=cwd) if cwd else room.dispatch(desk, text)


def _file_section(cwd, paths):
    parts, budget = [], ESCALATION_FILE_CHARS
    for rel in paths:
        try:
            with open(os.path.join(cwd, rel), encoding="utf-8") as fh:
                body = fh.read(budget)
        except (OSError, UnicodeDecodeError):
            continue
        parts.append(f"--- {rel} ---\n{body}")
        budget -= len(body)
        if budget <= 0:
            break
    return "\n\n".join(parts)


def _revert(cwd, patch):
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
        fh.write(patch)
        name = fh.name
    try:
        _sh(["git", "apply", "-R", "--whitespace=nowarn", name], cwd, 120)
    finally:
        os.unlink(name)


def escalate(task, cwd, run_id, failure, files, mode, *, test_cmd=None, kind="code",
             dispatcher=None):
    """Write the handoff pack; in auto mode send it to the Model Room desk for `kind`.

    Returns {"handoff", "desk", "outcome", "detail"}. Never raises on a desk problem."""
    done = _lib("jev_done_checks")
    pack = done.build_handoff(task, "", files, failure_output=failure)
    text = pack.get("pack") if isinstance(pack, dict) else None
    if not text:
        text = f"Task:\n{task}\n\nLast failure:\n{(failure or '')[-4000:]}"
    if test_cmd:
        text += f"\n\nThe task is done when this passes: {test_cmd}"
    section = _file_section(cwd, files)
    if section:
        text += "\n\nRelevant files as they stand now:\n\n" + section
    if kind == "code":
        text += ("\n\nMake the fix by editing the files directly in your working directory "
                 "(a disposable copy of the project) and run the test there. The local harness "
                 "reads your changes back, applies them to the real project and re-runs the test. "
                 "If you cannot edit, reply with ONE unified diff in a ```diff block instead.")
    else:
        text += f"\n\nThe project is at: {cwd}"
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    path = os.path.join(HANDOFF_DIR, f"{run_id}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    desk = ESCALATION_DESKS.get(kind, ESCALATION_DESKS["code"])
    result = {"handoff": path, "desk": desk, "outcome": "handoff_written", "detail": None}
    _say(f"handoff pack: {path}")
    if mode != "auto":
        _say(f"to escalate: send it to the {desk} Model Room desk (or rerun with --escalate auto)")
        return result
    _say(f"escalating to the {desk} Model Room desk")
    copy_dir = None
    try:
        if kind == "code":
            copy_dir = tempfile.mkdtemp(prefix=f"flash-escalate-{run_id}-")
            make_copy(cwd, copy_dir)
        send = dispatcher or _room_dispatch
        reply = send(desk, text, cwd=copy_dir) if copy_dir else send(desk, text)
        status = (reply or {}).get("status")
        answer = (reply or {}).get("result") or ""
        if answer:
            with open(path[:-3] + ".answer.md", "w", encoding="utf-8") as fh:
                fh.write(answer)
        patch = (read_patch(copy_dir) if copy_dir else "") or (
            extract_diff(answer) if kind == "code" else None)
    except Exception as exc:
        result.update(outcome="dispatch_failed", detail=f"{type(exc).__name__}: {exc}"[:300])
        _say(f"could not reach {desk}: {result['detail']}")
        return result
    finally:
        if copy_dir:
            shutil.rmtree(copy_dir, ignore_errors=True)
            shutil.rmtree(gitdir_for(copy_dir), ignore_errors=True)
    if not patch:
        result.update(outcome="dispatched", detail=status)
        return result
    ok, msg = apply_patch(cwd, patch)
    if not ok:
        result.update(outcome="desk_patch_did_not_apply", detail=msg[:300])
        return result
    if test_cmd:
        code, out = _sh(test_cmd, cwd, TEST_TIMEOUT)
        if code != 0:
            _revert(cwd, patch)
            result.update(outcome="desk_patch_failed_reverted", detail=out[-300:])
            _say(f"{desk}'s change failed the test and was reverted")
            return result
    result.update(outcome="fixed_by_desk", detail=status)
    _say(f"applied {desk}'s change" + (" and the test passes" if test_cmd else ""))
    return result


def cmd_run(a):
    cwd = os.path.abspath(a.cwd)
    task = a.task
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    row = {"run_id": run_id, "at": _now(), "cwd": cwd, "task": task[:2000], "test": a.test}
    intake = _lib("jev_intake")
    notebook = _lib("jev_notebook")

    sandbox = a.sandbox == "on" or (a.sandbox == "auto" and os.path.exists(SANDBOX_EXEC))
    if a.sandbox == "on" and not os.path.exists(SANDBOX_EXEC):
        _say("refusing: --sandbox on but sandbox-exec is not on this machine; model code does not run unsandboxed")
        row["outcome"] = "no_sandbox"
        _append(RUNS_LOG, row)
        return 2
    if a.sandbox == "auto" and not sandbox:
        _say("WARNING: no sandbox-exec on this machine; the attempt runs UNSANDBOXED (interactive use only)")
    row["sandbox"] = sandbox
    learn = not a.no_learn

    amb = intake.check_ambiguity(task)
    row["ambiguity"] = amb.get("verdict")
    if amb.get("verdict") in ("review_required", "unavailable") or (amb.get("escalate") and amb.get("verdict") != "ambiguous"):
        row.update(outcome="intake_review_required", intake_advice=amb)
        _say("ambiguity judgment requires review: " + json.dumps(amb.get("detail"))[:600])
        _append(RUNS_LOG, row)
        return 3
    if amb.get("verdict") == "ambiguous" and not a.force:
        _say("the task looks ambiguous: " + json.dumps(amb.get("detail"))[:600])
        _say("clarify it, or pass --force to run anyway")
        row["outcome"] = "ambiguous"
        _append(RUNS_LOG, row)
        return 3

    files = [f for f in _tracked_files(cwd)][:4000]
    route = intake.route_task(task, files=None, has_tests=bool(a.test))
    row["route"] = route.get("verdict")
    if route.get("verdict") in ("review_required", "unavailable") or (route.get("escalate") and route.get("verdict") != "escalate"):
        row.update(outcome="route_review_required", intake_advice=route)
        _say("route judgment requires review: " + json.dumps(route.get("detail"))[:600])
        _append(RUNS_LOG, row)
        return 4
    if route.get("verdict") == "escalate" and not a.force:
        _say("routed away from the local model: " + json.dumps(route.get("detail"))[:600])
        row["outcome"] = "routed_escalate"
        row["escalation"] = escalate(task, cwd, run_id, None, [], a.escalate, kind="judgment")
        row["handoff"] = row["escalation"]["handoff"]
        _append(RUNS_LOG, row)
        return 4

    effort_check = None if a.effort else intake.pick_effort(task)
    if effort_check and (effort_check.get("escalate") or effort_check.get("verdict") not in ("low", "medium", "high")):
        row.update(outcome="effort_review_required", intake_advice=effort_check)
        _say("effort judgment requires review: " + json.dumps(effort_check.get("detail"))[:600])
        _append(RUNS_LOG, row)
        return 4
    effort = a.effort or effort_check.get("verdict")
    if effort not in ("low", "medium", "high"):
        effort = "low"
    ctx = intake.pick_context(task, cwd)
    context_files = (ctx.get("detail") or {}).get("paths") or ctx.get("paths") or []
    recall = notebook.recall_mistakes(task)
    recalled = (recall.get("detail") or {}).get("lines") or recall.get("lines") or []
    example_text = None
    examples = _read_examples()
    if examples:
        pick = intake.pick_example(task, examples)
        chosen = (pick.get("detail") or {}).get("example_id")
        for ex in examples:
            if ex.get("id") == chosen:
                example_text = f"{ex.get('title')}\n{ex.get('summary')}"
    row.update(effort=effort, context=context_files[:10], recalled=len(recalled))
    rules, rules_error = ([], "skipped (--no-rules)") if a.no_rules else pick_rules(task)
    rules_text = rules_block(rules)
    row["rules"] = [r.get("id") for r in rules]
    if rules_error:
        row["rules_error"] = rules_error
    if rules:
        _say("rules for this task: " + ", ".join(f"{r.get('id')} {r.get('gist', '')[:50]}"
                                                  for r in rules))

    attempts = a.attempts or (3 if a.test else 1)
    prompt = build_prompt(task, a.test, context_files[:8], recalled[:3], example_text)
    workdir = tempfile.mkdtemp(prefix=f"flash-run-{run_id}-")
    candidates = []
    try:
        for n in range(1, attempts + 1):
            think = think_for(a.think, effort, n)
            _say(f"attempt {n}/{attempts} (effort {effort}, thinking {'on' if think else 'off'})")
            cand = run_attempt(n, cwd, retry_prompt(prompt, candidates[-1] if candidates else None),
                               a.test, effort, workdir, think=think, rules_text=rules_text, sandbox=sandbox)
            candidates.append(cand)
            status = "no test" if cand["test_exit_code"] is None else (
                "tests pass" if cand["test_exit_code"] == 0 else f"tests fail ({cand['test_exit_code']})")
            _say(f"  attempt {n}: {status}, {cand['probe_results']['patch_lines']} patch lines, "
                 f"{cand['elapsed_s']}s")
            # Early stop: a real test passing is the proof the extra attempts were buying.
            # --all-attempts keeps the full best-of-N when the tests are known to be thin.
            if not a.all_attempts and cand["test_exit_code"] == 0:
                break
        best = _lib("jev_best_of").select_candidate(
            task, [{k: c[k] for k in ("id", "code_or_diff", "probe_results", "test_output",
                                      "test_exit_code")} for c in candidates])
        chosen_id = best.get("verdict")
        row["selection"] = {"verdict": chosen_id, "confidence": best.get("confidence"),
                            "escalate": best.get("escalate"),
                            "advice": best.get("detail"),
                            "attempts": [{"id": c["id"], "test_exit_code": c["test_exit_code"],
                                          "elapsed_s": c["elapsed_s"]} for c in candidates]}
        if best.get("escalate") or chosen_id == "review_required":
            row["outcome"] = "selection_review_required"
            _say("candidate selection requires review; evidence retained in the run log")
            _append(RUNS_LOG, row)
            return 5
        chosen = next((c for c in candidates if c["id"] == chosen_id), None)
        if chosen is None and attempts == 1 and candidates and candidates[0]["patch"].strip() \
                and not a.test:
            chosen = candidates[0]
        if chosen is None or not chosen["patch"].strip():
            failure = candidates[-1]["test_output"] if candidates else ""
            _say("no attempt was good enough")
            if learn:  # queue runs pass --no-learn: posted task text must never seed a later prompt
                notebook.record_mistake("no_candidate", task,
                                        f"{attempts} attempts; last failure: {failure[-500:]}",
                                        "escalated", source=f"flash-run {run_id}")
            row["outcome"] = "no_candidate"
            row["escalation"] = escalate(task, cwd, run_id, failure, context_files[:8],
                                         a.escalate, test_cmd=a.test)
            row["handoff"] = row["escalation"]["handoff"]
            if row["escalation"]["outcome"] == "fixed_by_desk":
                row["outcome"] = "fixed_by_desk"
                _append(RUNS_LOG, row)
                return 0
            _append(RUNS_LOG, row)
            return 5

        if a.dry_run:
            print(chosen["patch"])
            row["outcome"] = "dry_run"
            _append(RUNS_LOG, row)
            return 0
        ok, msg = apply_patch(cwd, chosen["patch"])
        if not ok:
            _say(f"could not apply the chosen patch: {msg[:400]}")
            row["outcome"] = "apply_failed"
            _append(RUNS_LOG, row)
            return 5
        _say(f"applied {chosen['id']}")
        final_code = None
        if a.test:
            final_code, final_out = _run_test(a.test, cwd, TEST_TIMEOUT, sandbox=sandbox,
                                              reads=_dep_reads(cwd), execs=[os.path.join(cwd, ".venv", "bin")],
                                              port=flash_port())
            _say("tests pass in the real tree" if final_code == 0
                 else f"tests FAIL in the real tree ({final_code})\n{final_out[-1500:]}")
        review = _lib("jev_done_checks").triage_review(chosen["patch"][:40000], task)
        row["review"] = {"verdict": review.get("verdict"),
                         "advice": (review.get("detail") or {}).get("advice")}
        if review.get("verdict") == "needs_review":
            _say("review triage: this change touches risky code — have Claude review it: "
                 + str((review.get("detail") or {}).get("advice") or ""))
        row["outcome"] = "applied_pass" if final_code in (0, None) else "applied_fail"
        if final_code == 0 and learn:
            _append(EXAMPLES_LOG, {"id": run_id, "title": task[:120],
                                   "summary": chosen["patch"][:1500], "at": _now()})
        elif final_code is not None and learn:
            notebook.record_mistake("applied_but_failing", task, final_out[-800:],
                                    "needs follow-up", source=f"flash-run {run_id}")
        _append(RUNS_LOG, row)
        return 0 if final_code in (0, None) else 5
    finally:
        if not a.keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            _say(f"attempt copies kept in {workdir}")


def _read_examples(limit=200):
    rows = []
    try:
        with open(EXAMPLES_LOG, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


def cmd_plan(a):
    with open(a.file, encoding="utf-8") as fh:
        steps = [ln.strip().lstrip("-*0123456789. ").strip() for ln in fh
                 if ln.strip() and not ln.lstrip().startswith("#")]
    result = _lib("jev_intake").split_plan(steps)
    for step in (result.get("detail") or {}).get("steps") or result.get("steps") or []:
        print(f"{step.get('route', '?'):9} {step.get('difficulty', '')!s:6} {step.get('step', '')[:100]}")
    return 0


def cmd_scorecard(a):
    sc = _lib("jev_scorecard")
    suite = sc.load_suite(a.suite)
    results = []
    for task in suite:
        if a.only and task.get("id") not in a.only:
            continue
        _say(f"scorecard task {task.get('id')}")
        results.append(sc.run_task(task, attempts=a.attempts))
    summary = sc.summarize(results)
    print(json.dumps(summary, indent=2))
    _append(os.path.join(OUT, "flash-scorecard.jsonl"), {"at": _now(), "summary": summary})
    return 0


def cmd_stats(a):
    rows = []
    try:
        with open(RUNS_LOG, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except OSError:
        pass
    if a.since:
        rows = [r for r in rows if r.get("at", "") >= a.since]
    counts = {}
    for r in rows:
        counts[r.get("outcome", "?")] = counts.get(r.get("outcome", "?"), 0) + 1
    print(json.dumps({"runs": len(rows), "outcomes": counts,
                      "local_success_rate": round(counts.get("applied_pass", 0) / len(rows), 3)
                      if rows else None}, indent=2))
    return 0


def cmd_note(a):
    _lib("jev_notebook").record_mistake(a.kind, a.task or "", a.what, a.fix or "", source="manual")
    _say("noted")
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="flash-run", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run one task (default)")
    r.add_argument("task")
    r.add_argument("--cwd", default=".")
    r.add_argument("--test", default=None, help="the command that proves the task is done")
    r.add_argument("--attempts", type=int, default=None)
    r.add_argument("--effort", choices=["low", "medium", "high"], default=None)
    r.add_argument("--escalate", choices=["suggest", "auto"], default="suggest")
    r.add_argument("--all-attempts", action="store_true",
                   help="run every attempt even after one passes (slower, full best-of-N choice)")
    r.add_argument("--think", choices=["auto", "on", "off"], default="auto",
                   help="model thinking: auto = off on the first attempt of low-effort work, on for retries")
    r.add_argument("--force", action="store_true", help="run despite an ambiguity or routing stop")
    r.add_argument("--no-rules", action="store_true",
                   help="skip Jev's per-task rule pick (attempts get no RULES FOR THIS TASK block)")
    r.add_argument("--dry-run", action="store_true", help="print the chosen patch, do not apply")
    r.add_argument("--keep", action="store_true", help="keep the attempt copies")
    r.add_argument("--sandbox", choices=["auto", "on", "off"], default="auto",
                   help="run the agent and the tests under macOS sandbox-exec: auto = on if present (else a warning "
                        "and an unsandboxed run), on = required (refuse if absent), off = never. Queue runs pass on.")
    r.add_argument("--no-learn", action="store_true",
                   help="do not write the task to the examples log or the mistake notebook (queue runs pass this, "
                        "so untrusted room text can never seed a later prompt)")
    pl = sub.add_parser("plan")
    pl.add_argument("file")
    s = sub.add_parser("scorecard")
    s.add_argument("--suite", default=os.path.join(REPO, "ops", "config", "flash-scorecard-tasks.v1.json"))
    s.add_argument("--attempts", type=int, default=1)
    s.add_argument("--only", nargs="*")
    st = sub.add_parser("stats")
    st.add_argument("--since", default=None)
    n = sub.add_parser("note")
    n.add_argument("what")
    n.add_argument("--fix", default="")
    n.add_argument("--kind", default="manual")
    n.add_argument("--task", default="")
    known = {"run", "plan", "scorecard", "stats", "note", "-h", "--help"}
    if argv and argv[0] not in known:
        argv = ["run", *argv]
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    if a.cmd == "run" and not os.path.exists(FLASH):
        _say(f"the flash launcher is missing ({FLASH})")
        return 2
    return {"run": cmd_run, "plan": cmd_plan, "scorecard": cmd_scorecard,
            "stats": cmd_stats, "note": cmd_note}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
