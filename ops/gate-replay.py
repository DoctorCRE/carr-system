#!/usr/bin/env python3
"""gate-replay.py — run every hook gate over the committed replay fixtures and
compare the verdicts with a committed snapshot.

WHY THIS EXISTS. Defect class capability-reported-live-before-first-human-use
recurred eight times. Twice in one day a gate shipped green on invented input:
PR #1224's Stop gate parsed a transcript shape production never writes and
never fired on 803 real receipts; PR #1225's shell regexes were proven only on
made-up commands and a replay of 12,145 real ones found 62 false denials. The
first attempt at this check (the earlier version of PR #1226) tried to prove
coverage by reading selftest SOURCE TEXT against a diff from a base ref. An
Opus review found it a no-op in CI (the base ref did not exist in the runner
and git errors were swallowed), satisfiable by a comment, blind to imports and
config, and wrong about its own fixture counts. This replaces it with
behaviour, not text.

WHAT IT DOES, EVERY RUN, WITH NOTHING COMPUTED FROM A BASE REF:

  1. LEAK SCAN. Every file under the fixture directory and every fixture file
     the manifest names (any extension, `#` comment lines included), the
     manifest, and the verdict snapshot are scanned with
     ops/business_data_patterns.py: its shape patterns (emails, phone
     numbers, addresses, practice and title words, secrets and the rest) and,
     where a local roster file exists, the names on it. Any hit fails. The
     repository is public.
  2. MANIFEST COVERAGE. ops/config/gate-replay-manifest.json must name every
     hooks/*.py file (gate, helper, or wrapper). Every hook wiring in
     ops/config/hooks.json, .claude/settings.json and the tracked project
     settings under claude-tree/settings/ must appear as a manifest wiring
     with the same event and matcher, and every manifest wiring, no_replay
     ones included, must still exist there. no_replay is allowed only on
     SessionStart. A fixture set a wiring names must contain records its
     matcher actually selects.
  3. REPLAY. Each wiring's fixtures are fed to the gate as the harness feeds
     them: `python hooks/hook-meter-run.py hooks/<gate>.py` in a subprocess,
     the hook's JSON contract on stdin. The verdict is read from the telemetry
     line hook-meter-run.py itself writes (outcome, register, reopen,
     deny_class), so this check classifies exactly as production telemetry
     does. A gate that crashes, exits outside {0, 2}, times out, writes
     outside out/, or FAILS OPEN (logs its own ALLOW(internal-error),
     ALLOW(parse-error) and the like, then exits 0) fails CI.
  4. TEETH. Every synthetic scenario record must reach the verdict it
     `expect`s, and every replayed wiring must reach some verdict other than
     allow, or say why it never can (`allow_only`). Neither is satisfied by
     re-blessing the snapshot, so a gate that silently stops denying fails.
  5. HELPER EVIDENCE. Every Python module the gate processes execute is
     recorded by an audit hook. Each helper in hooks/ must be executed by some replayed
     gate, and the set of lib/*.py files executed must equal the manifest's
     lib_helpers list exactly.
  6. SNAPSHOT. The verdicts (one TSV row per non-allow verdict, plus a counts
     row per gate and event) must equal
     ops/fixtures/real-replay/verdict-snapshot.tsv. A change in any
     verdict fails CI until the snapshot is regenerated in the same pull
     request, so the reviewer reads the behaviour change as a diff.

DETERMINISM. The snapshot must be identical on a fresh Linux runner and on a
developer Mac with years of local state, so each worker replays inside its own
sandbox copy of the TRACKED tree only, untracked files never copied (with its
own single-commit git repository, never under a temp-directory prefix the gates
treat specially, which is refused outright, file times set
to the pinned instant), and every invocation gets a fresh HOME, TMPDIR and
out/. ops/gate_replay_shim/sitecustomize.py pins the clock, refuses sockets,
and records executed modules. A PATH shim turns network tools into failures.
Reason text is normalised (sandbox paths, digits, hex, UUIDs) before it is
compared.

USAGE:
    python3 ops/gate-replay.py            # CI: scan, check, replay, compare
    python3 ops/gate-replay.py --update   # rewrite the snapshot after a
                                          # deliberate verdict change
    python3 ops/gate-replay.py --jobs 8   # more workers (default: CPU count, max 8)

Selftest: ops/gate-replay-selftest.py. Fixtures: tools/extract-real-replay.py.
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ops import business_data_patterns as bdp  # noqa: E402
from ops import git_env  # noqa: E402

MANIFEST = REPO / "ops" / "config" / "gate-replay-manifest.json"
FIXTURE_DIR = REPO / "ops" / "fixtures" / "real-replay"
SNAPSHOT = FIXTURE_DIR / "verdict-snapshot.tsv"
SHIM_DIR = REPO / "ops" / "gate_replay_shim"
HOOKS_CONFIG = REPO / "ops" / "config" / "hooks.json"
PROJECT_SETTINGS = REPO / ".claude" / "settings.json"
# Tracked project settings the vault-rooted checkouts install (session-brief is
# wired only here). They are tracked, so every wiring in them is verified too.
CLAUDE_TREE_SETTINGS = REPO / "claude-tree" / "settings"
WIRED_IN = ("hooks.json", "project-settings")

SESSION_ID = bdp.REPLAY_SESSION_ID
INVOCATION_TIMEOUT = 45.0
NETWORK_TOOLS = ("gh", "curl", "wget", "ssh", "scp", "sftp", "rsync", "claude", "codex",
                 "psql", "osascript", "tailscale", "security", "open", "say", "pbcopy",
                 "pbpaste", "terminal-notifier", "nc", "ping", "dig", "nslookup")
SNAPSHOT_HEADER = (
    "# Verdict snapshot for ops/gate-replay.py. One row per gate, hook event and\n"
    "# fixture whose verdict is NOT allow, plus one `(all)` counts row per gate\n"
    "# and event; every fixture without a row was allowed. Regenerate after a\n"
    "# deliberate verdict change with:\n"
    "#     python3 ops/gate-replay.py --update\n"
    "# and commit it in the same pull request as the change.\n"
    "# gate\tevent\tfixture\tverdict\treason\n"
)
VERDICTS = ("allow", "announce", "ask", "deny", "reopen", "error")
# A commit of a few thousand files triggers `git gc --auto`, which DETACHES and
# repacks loose objects while the next step is still copying the sandbox — the
# first run of this file lost .git/objects/* mid-copy exactly that way. Every
# git call in a sandbox, the gates' own included, runs with both switched off.
NO_BACKGROUND_GIT = {"GIT_CONFIG_COUNT": "2",
                     "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
                     "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false"}


# ------------------------------------------------------------------ inputs

def load_manifest(path: Path = MANIFEST) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data: Dict[str, Any] = json.load(handle)
    return data


def load_fixtures(manifest: Dict[str, Any], fixture_dir: Path = FIXTURE_DIR) -> Dict[str, List[Dict[str, Any]]]:
    sets: Dict[str, List[Dict[str, Any]]] = {}
    for name, spec in manifest["fixture_sets"].items():
        rows = []
        with open(fixture_dir / spec["file"], encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        sets[name] = rows
    return sets


def epoch_of(stamp: str) -> float:
    return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))


# ------------------------------------------------------------------ leak scan

def leak_scan(paths: Iterable[Path]) -> List[str]:
    """One finding per (file, line, pattern). Never echoes the matched text.

    Every line of every file is scanned, `#` comment lines included, whatever
    the extension: a .json file is also scanned decoded, and each .jsonl line
    that parses is scanned decoded (keys and values one by one); any line that
    does not parse, and every line of any other file, is scanned as text.
    """
    findings: List[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            findings.append(f"{path.name}: unreadable ({getattr(err, 'strerror', None) or err})")
            continue
        if path.suffix == ".json":
            try:
                hits = bdp.scan_value(json.loads(text))
            except ValueError:
                hits = [("unparseable-json", "$")]
            findings.extend(f"{path.name}: {name} at {where}" for name, where in hits)
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            value: Any = None
            if path.suffix == ".jsonl" and not line.startswith("#"):
                try:
                    value = json.loads(line)
                except ValueError:
                    findings.append(f"{path.name}:{number}: not JSON")
            elif path.suffix == ".tsv" and not line.startswith("#"):
                value = line.split("\t")
            if value is None:
                if path.suffix == ".json":
                    continue  # already scanned decoded, and a raw JSON line is escaped text
                value = line
            for name, where in bdp.scan_value(value):
                findings.append(f"{path.name}:{number}: {name} at {where}")
    return findings


def leak_scan_targets(fixture_dir: Path = FIXTURE_DIR, manifest: Path = MANIFEST,
                      manifest_data: Optional[Dict[str, Any]] = None) -> List[Path]:
    """Every file in the fixture directory, every fixture file the manifest
    names wherever it lives, the manifest itself: no extension filter."""
    targets = {p for p in fixture_dir.rglob("*") if p.is_file()}
    for spec in (manifest_data or {}).get("fixture_sets", {}).values():
        if isinstance(spec, dict) and spec.get("file"):
            targets.add(fixture_dir / spec["file"])
    return sorted(targets) + [manifest]


# ------------------------------------------------------------------ manifest coverage

GATE_TOKEN = re.compile(r"hooks/([A-Za-z0-9_.-]+\.py)")
ENV_TOKEN = re.compile(r"\bCARR_CONTEXT_HOOK_EVENT=(\w+)")


def wiring_key(gate: str, event: str, matcher: str, env_event: str = "") -> Tuple[str, str, str, str]:
    return (gate, event, matcher, env_event)


def config_wirings(hooks_config: Path = HOOKS_CONFIG,
                   project_settings: Path = PROJECT_SETTINGS,
                   tree_settings: Optional[Path] = CLAUDE_TREE_SETTINGS) -> Dict[Tuple[str, str, str, str], str]:
    """Every (gate, event, matcher, context-env) wired in tracked hook config,
    mapped to the file it came from. For a gate run through run-record-gate.py
    the wired gate is the argument, which is what the harness actually runs."""
    found: Dict[Tuple[str, str, str, str], str] = {}
    sources = [(hooks_config, "ops/config/hooks.json"), (project_settings, ".claude/settings.json")]
    if tree_settings is not None and tree_settings.is_dir():
        # user.settings.json is an unreferenced snapshot of USER-level
        # settings; the user level is generated from ops/config/hooks.json,
        # which is already read above. The rest are project settings.
        sources += [(p, f"claude-tree/settings/{p.name}")
                    for p in sorted(tree_settings.glob("*.settings.json"))
                    if p.name != "user.settings.json"]
    for source, label in sources:
        try:
            with open(source, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        blocks = data.get("hooks", data) if isinstance(data, dict) else {}
        for event, groups in blocks.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                matcher = str(group.get("matcher", "") or "")
                for hook in group.get("hooks", []) or []:
                    command = str(hook.get("command", ""))
                    names = GATE_TOKEN.findall(command)
                    names = [n for n in names if n != "hook-meter-run.py"]
                    tail = command.split("run-record-gate.py", 1)
                    if len(tail) == 2 and tail[1].strip():
                        names = [tail[1].strip().split()[0]]
                    env = ENV_TOKEN.search(command)
                    for name in names[:1]:
                        found[wiring_key(name, event, matcher, env.group(1) if env else "")] = label
    return found


def check_manifest(manifest: Dict[str, Any], fixtures: Dict[str, List[Dict[str, Any]]],
                   hooks_dir: Path, wired: Dict[Tuple[str, str, str, str], str]) -> List[str]:
    errors: List[str] = []
    entries: Dict[str, Any] = manifest.get("hooks", {})
    on_disk = sorted(p.name for p in hooks_dir.glob("*.py"))
    for name in on_disk:
        if name not in entries:
            errors.append(f"hooks/{name} is not in the manifest: add it to "
                          f"ops/config/gate-replay-manifest.json as a gate (with its wirings), "
                          f"a helper, or a wrapper")
    for name in entries:
        if name not in on_disk:
            errors.append(f"manifest names hooks/{name}, which does not exist")
    declared: Set[Tuple[str, str, str, str]] = set()
    sets = manifest["fixture_sets"]
    for name, entry in sorted(entries.items()):
        role = entry.get("role")
        if role not in ("gate", "helper", "wrapper"):
            errors.append(f"{name}: role must be gate, helper or wrapper")
            continue
        if role != "gate":
            continue
        wirings = entry.get("wirings") or []
        if not wirings:
            errors.append(f"{name}: a gate needs at least one wiring")
        for wiring in wirings:
            env_event = str((wiring.get("env") or {}).get("CARR_CONTEXT_HOOK_EVENT", ""))
            key = wiring_key(name, wiring["event"], wiring.get("matcher", ""), env_event)
            declared.add(key)
            where = wiring.get("wired_in", "hooks.json")
            if where not in WIRED_IN:
                errors.append(f"{name}: wired_in must be one of {', '.join(WIRED_IN)}, not {where!r}: "
                              "a wiring the tracked config cannot confirm is not exempt")
            if key not in wired:
                errors.append(f"{name}: manifest wiring {wiring['event']} '{wiring.get('matcher', '')}' "
                              "is not wired in ops/config/hooks.json, .claude/settings.json or "
                              "claude-tree/settings/")
            if wiring.get("no_replay") and wiring["event"] != "SessionStart":
                errors.append(f"{name} {wiring['event']}: no_replay is allowed only on SessionStart; "
                              "every other event has a payload a fixture can carry")
            if wiring.get("no_replay"):
                if wiring.get("fixtures"):
                    errors.append(f"{name}: a no_replay wiring must not list fixtures")
                continue
            if not wiring.get("fixtures"):
                errors.append(f"{name} {wiring['event']}: no fixtures and no no_replay reason")
                continue
            selected = 0
            for set_name in wiring["fixtures"]:
                if set_name not in sets:
                    errors.append(f"{name}: unknown fixture set {set_name}")
                    continue
                selected += len(select(fixtures.get(set_name, []), sets[set_name], wiring, name))
            if selected == 0:
                errors.append(f"{name} {wiring['event']} '{wiring.get('matcher', '')}': "
                              f"its fixture sets hold no record its matcher selects")
    for key, label in sorted(wired.items()):
        if key not in declared:
            gate, event, matcher, env_event = key
            extra = f" (CARR_CONTEXT_HOOK_EVENT={env_event})" if env_event else ""
            errors.append(f"{label} wires hooks/{gate} on {event} '{matcher}'{extra}, "
                          f"and the manifest has no such wiring")
    return errors


def select(records: List[Dict[str, Any]], spec: Dict[str, Any], wiring: Dict[str, Any],
           gate: str = "") -> List[Dict[str, Any]]:
    """The records of one fixture set that this wiring's matcher selects. A
    scenario record names the one gate and event it was written for."""
    if spec.get("kind") == "scenario":
        return [r for r in records if r.get("gate") == gate and r.get("event") == wiring.get("event")
                and r.get("matcher", wiring.get("matcher", "")) == wiring.get("matcher", "")]
    if spec.get("kind") != "tool":
        return records
    matcher = wiring.get("matcher", "")
    if not matcher:
        return records
    pattern = re.compile(matcher)
    return [r for r in records if pattern.fullmatch(str(r.get("tool_name", "")))]


# ------------------------------------------------------------------ sandbox

@dataclass
class Worker:
    index: int
    root: Path
    repo: Path
    home: Path
    tmp: Path
    state: Path
    stubs: Path

    @property
    def telemetry(self) -> Path:
        return self.state / "telemetry.jsonl"

    @property
    def trace(self) -> Path:
        return self.state / "opened.txt"

    @property
    def transcript(self) -> Path:
        return self.state / "transcript.jsonl"


def tracked_files(repo: Path) -> List[str]:
    """Only what git tracks (the index). Untracked files are never copied: a
    replay must see what CI sees, and an untracked scratch file (a local
    roster, a draft) must never shape a verdict or reach the sandbox."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=repo, env=git_env.scrubbed_env(), capture_output=True, check=True)
    names = [n for n in out.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
    return sorted(set(names))


def build_sandbox(run_root: Path, pinned: float, source: Path = REPO) -> Path:
    """A private copy of the tracked tree with its own one-commit repository."""
    repo = run_root / "w0" / "repo"
    repo.mkdir(parents=True)
    for name in tracked_files(source):
        src = source / name
        if not src.is_file() and not src.is_symlink():
            continue
        dst = repo / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)
            os.utime(dst, (pinned, pinned))
    env = git_env.fixture_env()
    env.update(NO_BACKGROUND_GIT)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(pinned - 86400))
    env.update({"GIT_AUTHOR_NAME": "gate-replay", "GIT_AUTHOR_EMAIL": "gate-replay@invalid",
                "GIT_COMMITTER_NAME": "gate-replay", "GIT_COMMITTER_EMAIL": "gate-replay@invalid",
                "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    for args in (["init", "-q", "-b", "replay"], ["add", "-A"],
                 ["commit", "-q", "--no-verify", "--no-gpg-sign", "-m", "gate-replay sandbox"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    # The wired commands name {{REPO}}/.venv/bin/python, and run-record-gate.py
    # refuses to start without it. Production has that virtualenv; so does the
    # sandbox, as a launcher for the interpreter running this check. It is a
    # launcher rather than a link because run-record-gate.py deletes PYTHONPATH
    # before it execs the gate, which would drop the replay shim (clock, network
    # refusal, module trace) for exactly the two gates it wraps. .venv is
    # gitignored, so none of this reads as a tree change.
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    launcher = ("#!/bin/sh\n"
                f"PYTHONPATH={shlex.quote(str(SHIM_DIR))} exec {shlex.quote(sys.executable)} \"$@\"\n")
    for name in ("python", "python3"):
        (venv_bin / name).write_text(launcher)
        (venv_bin / name).chmod(0o755)
    return repo


def make_worker(run_root: Path, index: int, template: Path) -> Worker:
    root = run_root / f"w{index}"
    repo = root / "repo"
    if index:
        shutil.copytree(template, repo, symlinks=True)
    stubs = root / "stubs"
    stubs.mkdir(parents=True, exist_ok=True)
    for tool in NETWORK_TOOLS:
        stub = stubs / tool
        stub.write_text("#!/bin/sh\necho \"gate-replay sandbox: $(basename \"$0\") is disabled\" >&2\nexit 1\n")
        stub.chmod(0o755)
    worker = Worker(index, root, repo, root / "home", root / "tmp", root / "state", stubs)
    worker.state.mkdir(parents=True, exist_ok=True)
    return worker


def reset(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def prepare_invocation(worker: Worker) -> None:
    for path in (worker.home, worker.tmp, worker.repo / "out", worker.telemetry, worker.trace,
                 worker.transcript, worker.state / "guard.log"):
        reset(path)
    worker.home.mkdir()
    worker.tmp.mkdir()
    # ~/carr-system is where the canonical checkout lives in production; point
    # it at this sandbox so a gate resolving that path reads the same tree.
    os.symlink(worker.repo, worker.home / "carr-system")


# ------------------------------------------------------------------ payloads

@dataclass
class Invocation:
    gate: str
    event: str
    matcher: str
    fixture_set: str
    record: Dict[str, Any]
    via: Optional[str]
    env: Dict[str, str]
    clock_label: str
    clock: float
    shape: Optional[str] = None

    @property
    def fixture_key(self) -> str:
        key = f"{self.fixture_set}:{self.record['id']}"
        if self.shape:
            key += f"/{self.shape}"
        if self.clock_label:
            key += f"@{self.clock_label}"
        return key

    @property
    def row_event(self) -> str:
        return self.event if not self.matcher else f"{self.event}[{self.matcher}]"


# Placeholders a fixture may use for text the public-repo leak scan must never
# see literally. The only one is the public vendor domain costar-lane-gate.py
# itself names (BANNED_DOMAIN there): a hostname in a fixture always fails the
# scan, so the scenario that proves the gate denies it spells it this way.
PLACEHOLDERS = {"{{BANNED_VENDOR_HOST}}": "costar" + "." + "com"}


def substitute(value: Any, repo: str, home: str) -> Any:
    if isinstance(value, str):
        value = value.replace("{{REPO}}", repo).replace("{{HOME}}", home)
        for token, text in PLACEHOLDERS.items():
            value = value.replace(token, text)
        return value
    if isinstance(value, list):
        return [substitute(v, repo, home) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, repo, home) for k, v in value.items()}
    return value


def iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def transcript_lines(inv: Invocation, worker: Worker, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    repo, home = str(worker.repo), str(worker.home)
    base = {"isSidechain": False, "userType": "external", "cwd": repo, "sessionId": SESSION_ID,
            "version": "2.1.0", "gitBranch": "replay"}
    rid = inv.record["id"]
    lines: List[Dict[str, Any]] = []
    parent: Optional[str] = None

    def add(kind: str, offset: int, body: Dict[str, Any]) -> None:
        nonlocal parent
        uid = f"00000000-0000-4000-8000-{len(lines) + 1:012d}"
        line = {**base, "type": kind, "uuid": uid, "parentUuid": parent,
                "timestamp": iso(inv.clock - offset), **body}
        lines.append(line)
        parent = uid

    # A scenario may open the transcript with raw records (a scheduled run's
    # queue-operation launch marker) and name the model the turn ran on.
    for raw in inv.record.get("raw_head") or []:
        lines.append(substitute(raw, repo, home))
    model = str(inv.record.get("model") or "claude-opus-5-5")

    def assistant(offset: int, content: List[Dict[str, Any]], suffix: str) -> None:
        add("assistant", offset, {"message": {"role": "assistant", "model": model,
                                              "id": f"msg_replay_{rid}_{suffix}", "type": "message",
                                              "content": content}})

    def tool_step(offset: int, name: str, tool_input: Any, result: str, suffix: str) -> None:
        use_id = f"toolu_replay_{rid}_{suffix}"
        assistant(offset, [{"type": "tool_use", "id": use_id, "name": name,
                            "input": substitute(tool_input, repo, home)}], suffix)
        add("user", offset - 1, {"message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": use_id, "content": result}]},
            "toolUseResult": {"stdout": result, "stderr": "", "interrupted": False}})

    if not inv.record.get("no_prompt"):
        # A scenario may omit the partner's prompt (an unattended run, a
        # session a scheduled task started): some gates act only then.
        prompt = inv.record.get("prompt") or manifest["turn_prompt"]
        add("user", 120, {"message": {"role": "user", "content": prompt}, "promptId": f"replay-{rid}"})
    if inv.fixture_set in manifest["fixture_sets"] and manifest["fixture_sets"][inv.fixture_set]["kind"] == "turn":
        # The REAL advisory record sits where the harness put it, straight
        # after the prompt; the rest of the turn is one of the manifest's
        # synthetic turn shapes, so every advisory is replayed against a turn
        # that edits, verifies, claims or asks.
        attachment = {"type": "hook_additional_context",
                      "content": substitute(inv.record["content"], repo, home),
                      "hookName": inv.record.get("hookName") or inv.record.get("hookEvent"),
                      "hookEvent": inv.record.get("hookEvent"),
                      "toolUseID": f"toolu_replay_{rid}"}
        add("attachment", 110, {"attachment": attachment})
        shape = manifest["stop_turn_shapes"][inv.shape or ""]
        for index, step in enumerate(shape["steps"]):
            tool_step(100 - 10 * index, step["tool_name"], step["tool_input"],
                      step.get("result", ""), f"s{index}")
        assistant(5, [{"type": "text", "text": shape["final"]}], "final")
        return lines
    if inv.fixture_set in manifest["fixture_sets"] and manifest["fixture_sets"][inv.fixture_set]["kind"] == "scenario":
        # A synthetic scenario written to drive one gate down its deny or
        # reopen path: its own prompt, an optional advisory, its own steps
        # and final text, then (for a tool event) the call under judgment.
        if inv.record.get("attachment"):
            add("attachment", 110, {"attachment": substitute(inv.record["attachment"], repo, home)})
        for index, step in enumerate(inv.record.get("steps") or []):
            tool_step(100 - 10 * index, step["tool_name"], step["tool_input"],
                      step.get("result", ""), f"s{index}")
        if inv.record.get("final"):
            assistant(6, [{"type": "text", "text": inv.record["final"]}], "final")
    if "tool_name" in inv.record:
        # The call under judgment is already in the transcript when PreToolUse
        # fires, exactly as in a live session.
        assistant(5, [{"type": "tool_use", "id": f"toolu_replay_{rid}",
                       "name": inv.record["tool_name"],
                       "input": substitute(inv.record["tool_input"], repo, home)}], "call")
    return lines


def tool_response(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "Bash":
        return {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}
    if tool_name in ("Edit", "Write", "MultiEdit"):
        return {"filePath": tool_input.get("file_path", ""), "success": True}
    if tool_name == "Read":
        return {"type": "text", "file": {"filePath": tool_input.get("file_path", ""),
                                         "content": "", "numLines": 0, "startLine": 1, "totalLines": 0}}
    return {}


def payload(inv: Invocation, worker: Worker) -> Dict[str, Any]:
    repo, home = str(worker.repo), str(worker.home)
    data: Dict[str, Any] = {"session_id": SESSION_ID, "transcript_path": str(worker.transcript),
                            "cwd": repo, "permission_mode": "default", "hook_event_name": inv.event}
    if inv.event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        tool_input = substitute(inv.record["tool_input"], repo, home)
        data.update(tool_name=inv.record["tool_name"], tool_input=tool_input,
                    tool_use_id=f"toolu_replay_{inv.record['id']}")
        if inv.event == "PostToolUse":
            data["tool_response"] = tool_response(inv.record["tool_name"], tool_input)
        elif inv.event == "PostToolUseFailure":
            data["error"] = "replayed tool failure"
    elif inv.event == "UserPromptSubmit":
        data["prompt"] = inv.record["prompt"]
    elif inv.event in ("Stop", "SubagentStop"):
        data["stop_hook_active"] = False
    elif inv.event == "PreCompact":
        data.update(trigger="auto", custom_instructions="")
    return data


def invocations(manifest: Dict[str, Any], fixtures: Dict[str, List[Dict[str, Any]]]) -> List[Invocation]:
    pinned = epoch_of(manifest["pinned_utc"])
    shapes = sorted(manifest["stop_turn_shapes"])
    out: List[Invocation] = []
    for gate, entry in sorted(manifest["hooks"].items()):
        if entry.get("role") != "gate":
            continue
        for wiring in entry.get("wirings", []):
            if wiring.get("no_replay"):
                continue
            for set_name in wiring["fixtures"]:
                spec = manifest["fixture_sets"][set_name]
                for record in select(fixtures[set_name], spec, wiring, gate):
                    shape = None
                    clocks = wiring.get("clocks") or {"": manifest["pinned_utc"]}
                    if spec["kind"] == "turn":
                        shape = shapes[int(record["id"], 16) % len(shapes)]
                    if spec["kind"] == "scenario" and record.get("clock"):
                        clocks = {record.get("clock_label", "scenario"): record["clock"]}
                    for label, stamp in sorted(clocks.items()):
                        out.append(Invocation(
                            gate=gate, event=wiring["event"], matcher=wiring.get("matcher", ""),
                            fixture_set=set_name, record=record, via=wiring.get("via"),
                            env={**(wiring.get("env") or {}),
                                 **((record.get("env") or {}) if spec["kind"] == "scenario" else {})},
                            clock_label=label,
                            clock=epoch_of(stamp) if stamp else pinned, shape=shape))
    return out


# ------------------------------------------------------------------ one run

@dataclass
class Result:
    inv: Invocation
    verdict: str
    reason: str
    detail: str = ""
    opened: Set[str] = field(default_factory=set)
    seconds: float = 0.0

    @property
    def row(self) -> Tuple[str, ...]:
        return (self.inv.gate, self.inv.row_event, self.inv.fixture_key, self.verdict, self.reason)


UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
HEX_RE = re.compile(r"(?i)\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b")
DIGITS_RE = re.compile(r"\d+")


REASON_KEYS = ("schema", "status", "reason", "effect", "decision", "mode")


def json_reason(text: str) -> Optional[str]:
    """A structured announcement reduced to the fields that say what it is,
    so its reason reads as `json schema=... status=...` instead of a digest-
    laden first line."""
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    advisory = data.get("advisory")
    inner: Dict[str, Any] = advisory if isinstance(advisory, dict) else {}
    parts = []
    for source in (data, inner):
        for key in REASON_KEYS:
            value = source.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value):
                parts.append(f"{key}={value}")
    if not parts:
        parts.append("keys=" + ",".join(sorted(data)[:6]))
    return "json " + " ".join(parts)


def normalise(text: str, worker: Worker) -> str:
    line = json_reason(text) or next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    if not line:
        return "-"
    swaps = [(str(worker.repo), "{REPO}"), (os.path.realpath(worker.repo), "{REPO}"),
             (str(worker.home), "{HOME}"), (os.path.realpath(worker.home), "{HOME}"),
             (str(worker.tmp), "{TMP}"), (os.path.realpath(worker.tmp), "{TMP}"),
             (str(worker.state), "{STATE}"), (os.path.realpath(worker.state), "{STATE}"),
             (str(worker.root), "{RUN}"), (os.path.realpath(worker.root), "{RUN}")]
    for real, token in sorted(swaps, key=lambda s: -len(s[0])):
        line = line.replace(real, token)
    line = UUID_RE.sub("<uuid>", line)
    line = HEX_RE.sub("<hex>", line)
    line = DIGITS_RE.sub("#", line)
    line = " ".join(line.replace("\t", " ").split())
    return line[:140]


def reason_text(stdout: str, stderr: str, deny_class: Optional[str]) -> str:
    if deny_class:
        return f"class:{deny_class}"
    stripped = (stdout or "").strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            hso = data.get("hookSpecificOutput")
            specific: Dict[str, Any] = hso if isinstance(hso, dict) else {}
            for candidate in (data.get("reason"), specific.get("permissionDecisionReason"),
                              specific.get("additionalContext"), data.get("systemMessage"),
                              data.get("stopReason")):
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
    for stream in (stderr, stdout):
        if stream and stream.strip():
            return stream
    return ""


_METER: Any = None


def meter_module() -> Any:
    """hooks/hook-meter-run.py, loaded once for its classification functions."""
    global _METER
    if _METER is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gate_replay_hook_meter_run", REPO / "hooks" / "hook-meter-run.py")
        if spec is None or spec.loader is None:
            raise ImportError("cannot load hooks/hook-meter-run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _METER = module
    return _METER


def wrapper_row(stdout: str, stderr: str, code: int, event: str) -> Dict[str, Any]:
    """The telemetry fields hook-meter-run.py would have recorded, computed by
    its own functions, for a gate that exec'd away from the wrapper."""
    meter = meter_module()
    published = meter._decision_from_output(stdout)
    crashed = code not in (0, 2)
    if crashed:
        outcome = "error"
    elif code == 2:
        outcome = "deny"
    else:
        outcome = published or "allow"
    return {"outcome": outcome, "exit": code,
            "register": meter._register_from_output(stdout, event, code, crashed),
            "reopen": bool(event in meter.STOP_EVENTS and outcome == "deny"),
            "deny_class": (meter._deny_class(stderr) or meter._deny_class(stdout)
                           or meter._structured_reason(stdout)),
            "error_tail": meter._error_tail(stderr) if crashed else None}


def classify(row: Dict[str, Any]) -> str:
    outcome = row.get("outcome")
    register = row.get("register")
    if outcome == "error":
        return "error"
    if row.get("reopen") or register == "reopen":
        return "reopen"
    if outcome in ("deny", "ask"):
        return str(outcome)
    if register == "block":
        return "deny"
    if register == "announce":
        return "announce"
    return "allow"


# A gate that catches its own exception logs ALLOW(internal-error),
# ALLOW(parse-error), shadow-writing ALLOW(import-failed) and so on, then
# exits 0. Other ALLOW(...) tags (outside-repo, worktree, no-transcript) are
# ordinary decisions and do not match.
FAIL_OPEN_RE = re.compile(r"ALLOW\(([a-z-]*(?:error|fail(?:ed|ure)?)[a-z-]*)\)")


def fail_open_lines(worker: Worker) -> List[str]:
    """The fail-open tags any log this invocation wrote carries. Looks in the
    guard log, out/ and HOME, without following the ~/carr-system symlink."""
    tags: List[str] = []
    roots = [worker.state / "guard.log", worker.repo / "out", worker.home, worker.tmp]
    for root in roots:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = [Path(d) / f for d, _dirs, names in os.walk(root, followlinks=False) for f in names]
        else:
            continue
        for path in files:
            try:
                if path.is_symlink() or path.stat().st_size > 4_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in FAIL_OPEN_RE.finditer(text):
                tag = f"ALLOW({match.group(1)}) in {path.name}"
                if tag not in tags:
                    tags.append(tag)
    return tags


def seed_scenario(inv: Invocation, worker: Worker) -> None:
    """Lay down the state a scenario says an EARLIER turn or hook left behind.

    Only per-invocation places are writable: HOME and out/ are both reset
    before every invocation, so nothing seeded here outlives it or reaches
    the tracked tree. home_git_repos makes a second git working tree for the
    gates whose policy is about code landing outside this repository."""
    record = inv.record
    repo, home = str(worker.repo), str(worker.home)
    for base, files in ((worker.home, record.get("home_files")),
                        (worker.repo / "out", record.get("out_files"))):
        for rel, content in (files or {}).items():
            target = (base / rel).resolve()
            if not str(target).startswith(str(base.resolve()) + os.sep):
                raise ValueError(f"scenario file escapes its root: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = content if isinstance(content, str) else json.dumps(content)
            target.write_text(substitute(text, repo, home), encoding="utf-8")
    for rel in record.get("home_git_repos") or []:
        target = worker.home / rel
        target.mkdir(parents=True, exist_ok=True)
        env = git_env.fixture_env()
        env.update(NO_BACKGROUND_GIT)
        subprocess.run(["git", "init", "-q", "-b", "other"], cwd=target, env=env,
                       check=True, capture_output=True)


def run_one(inv: Invocation, worker: Worker, manifest: Dict[str, Any]) -> Result:
    started = time.monotonic()
    prepare_invocation(worker)
    seed_scenario(inv, worker)
    with open(worker.transcript, "w", encoding="utf-8") as handle:
        for line in transcript_lines(inv, worker, manifest):
            handle.write(json.dumps(line) + "\n")
    env = {
        "PATH": f"{worker.stubs}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(worker.home), "TMPDIR": str(worker.tmp), "TZ": "UTC",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "USER": "replay", "LOGNAME": "replay",
        "PYTHONPATH": str(SHIM_DIR), "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "CARR_HOOK_TELEMETRY": str(worker.telemetry),
        "CARR_HOOK_GUARD_LOG": str(worker.state / "guard.log"),
        "CARR_GATE_REPLAY_EPOCH": repr(inv.clock),
        "CARR_GATE_REPLAY_ROOT": str(worker.repo),
        "CARR_GATE_REPLAY_TRACE": str(worker.trace),
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        **NO_BACKGROUND_GIT,
        **{k: substitute(str(v), str(worker.repo), str(worker.home)) for k, v in inv.env.items()},
    }
    target = worker.repo / "hooks" / (inv.via or inv.gate)
    argv = [sys.executable, str(worker.repo / "hooks" / "hook-meter-run.py"), str(target)]
    if inv.via:
        argv.append(inv.gate)
    body = json.dumps(payload(inv, worker)).encode("utf-8")
    try:
        proc = subprocess.run(argv, input=body, capture_output=True, env=env,
                              cwd=str(worker.repo), timeout=INVOCATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        return Result(inv, "error", "timeout", f"no verdict within {INVOCATION_TIMEOUT:.0f}s",
                      seconds=time.monotonic() - started)
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    telemetry: Optional[Dict[str, Any]] = None
    try:
        for raw_line in worker.telemetry.read_text(encoding="utf-8").splitlines():
            row = json.loads(raw_line)
            if row.get("hook") == target.name:
                telemetry = row
    except (OSError, ValueError):
        telemetry = None
    opened: Set[str] = set()
    try:
        opened = {ln for ln in worker.trace.read_text(encoding="utf-8").splitlines() if ln}
    except OSError:
        pass
    if telemetry is None:
        if not inv.via:
            return Result(inv, "error", "no-telemetry",
                          f"hook-meter-run.py wrote no telemetry line (exit {proc.returncode})\n"
                          + "\n".join(stderr.strip().splitlines()[-6:]),
                          opened=opened, seconds=time.monotonic() - started)
        # run-record-gate.py os.execve()s its gate, so the wrapper process is
        # replaced and never writes its line; production telemetry has the same
        # hole. Classify the exec'd gate's exit and output with the wrapper's
        # OWN functions instead, so the verdict still means what telemetry means.
        telemetry = wrapper_row(stdout, stderr, proc.returncode, inv.event)
    verdict = classify(telemetry)
    fail_open = fail_open_lines(worker)
    if fail_open:
        # The gate caught its own crash and allowed: exit 0, no telemetry
        # error, but its log says ALLOW(internal-error) or the like. That is a
        # crash the harness would never see, so it is a crash here.
        return Result(inv, "error", "fail-open",
                      "the gate failed open: " + " | ".join(fail_open[:3]),
                      opened=opened, seconds=time.monotonic() - started)
    detail = ""
    if verdict == "error":
        detail = f"exit {telemetry.get('exit')}\n{telemetry.get('error_tail') or stderr[-1200:]}"
    reason = "-" if verdict == "allow" else normalise(
        reason_text(stdout, stderr, telemetry.get("deny_class")), worker)
    return Result(inv, verdict, reason, detail, opened, time.monotonic() - started)


# ------------------------------------------------------------------ the run

@dataclass
class Report:
    results: List[Result]
    seconds: float
    sandbox_seconds: float
    mutated: List[str]


class WorkdirRefused(Exception):
    pass


def temp_prefixes() -> List[str]:
    prefixes = {"/tmp", "/var/tmp", "/var/folders", "/private/tmp", "/private/var/tmp",
                "/private/var/folders", "/dev/shm", tempfile.gettempdir()}
    if os.environ.get("TMPDIR"):
        prefixes.add(os.environ["TMPDIR"])
    resolved = set()
    for prefix in prefixes:
        resolved.add(prefix.rstrip("/"))
        resolved.add(os.path.realpath(prefix).rstrip("/"))
    return sorted(p for p in resolved if p)


def replay_workdir() -> Path:
    """Where sandboxes are built: CARR_GATE_REPLAY_WORKDIR or ~/.cache.

    Never under a temp prefix. Many gates treat a path under /tmp (or the
    platform temp dir) as a fixture or scratch path and wave it through, so a
    sandbox there would replay the exempt branch of every such gate instead
    of the production one, and the snapshot would record the wrong verdicts."""
    chosen = (os.environ.get("CARR_GATE_REPLAY_WORKDIR")
              or os.path.join(os.path.expanduser("~"), ".cache", "carr-gate-replay"))
    real = os.path.realpath(chosen)
    for prefix in temp_prefixes():
        for candidate in (os.path.abspath(chosen), real):
            if candidate == prefix or candidate.startswith(prefix + "/"):
                raise WorkdirRefused(
                    f"replay workdir {chosen} is under the temp prefix {prefix}; gates treat "
                    "temp paths as scratch, so the replay would not see production verdicts. "
                    "Set CARR_GATE_REPLAY_WORKDIR to a directory outside any temp prefix.")
    return Path(chosen)


def replay(manifest: Dict[str, Any], fixtures: Dict[str, List[Dict[str, Any]]], jobs: int,
           keep: bool = False, source: Path = REPO, progress: bool = True) -> Report:
    work = invocations(manifest, fixtures)
    base_dir = replay_workdir()
    base_dir.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="run-", dir=str(base_dir))).resolve()
    started = time.monotonic()
    try:
        pinned = epoch_of(manifest["pinned_utc"])
        template = build_sandbox(run_root, pinned, source)
        workers = [make_worker(run_root, i, template) for i in range(max(1, jobs))]
        sandbox_seconds = time.monotonic() - started
        pool: "queue.Queue[Worker]" = queue.Queue()
        for worker in workers:
            pool.put(worker)
        lock = threading.Lock()
        done = [0]

        def task(inv: Invocation) -> Result:
            worker = pool.get()
            try:
                return run_one(inv, worker, manifest)
            finally:
                pool.put(worker)
                with lock:
                    done[0] += 1
                    if progress and done[0] % 500 == 0:
                        print(f"  gate-replay: {done[0]}/{len(work)} invocations", file=sys.stderr)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
            results = list(executor.map(task, work))
        mutated: List[str] = []
        for worker in workers:
            reset(worker.repo / "out")
            status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                                    cwd=worker.repo, env=git_env.fixture_env(),
                                    capture_output=True, text=True)
            mutated.extend(f"w{worker.index}: {line}" for line in status.stdout.splitlines())
        return Report(results, time.monotonic() - started, sandbox_seconds, mutated)
    finally:
        if keep:
            print(f"gate-replay: sandbox kept at {run_root}", file=sys.stderr)
        else:
            shutil.rmtree(run_root, ignore_errors=True)


def helper_errors(manifest: Dict[str, Any], results: List[Result]) -> List[str]:
    opened: Set[str] = set()
    for result in results:
        opened |= result.opened
    errors = []
    for name, entry in sorted(manifest["hooks"].items()):
        if entry.get("role") == "helper" and f"hooks/{name}" not in opened:
            errors.append(f"hooks/{name} is listed as a helper, but no replayed gate executed it")
    lib_opened = {p for p in opened if p.startswith("lib/") and p.endswith(".py") and "/" not in p[4:]}
    listed = set(manifest.get("lib_helpers", []))
    for path in sorted(lib_opened - listed):
        errors.append(f"{path} is executed by a replayed gate but is not in the manifest's lib_helpers")
    for path in sorted(listed - lib_opened):
        errors.append(f"{path} is in the manifest's lib_helpers, but no replayed gate executed it")
    return errors


def behaviour_errors(manifest: Dict[str, Any], results: List[Result]) -> List[str]:
    """Two checks a snapshot re-bless cannot satisfy.

    1. Every scenario record reaches the verdict it `expect`s. A scenario is
       written to drive a gate down its deny or reopen path, so a gate that
       stops denying fails here even if someone rewrites the snapshot.
    2. Every replayed gate wiring reaches some verdict other than allow, or
       its manifest wiring says why it never can (`allow_only`). A wiring that
       only ever allows proves nothing about the gate's teeth."""
    errors: List[str] = []
    sets = manifest["fixture_sets"]
    seen: Dict[Tuple[str, str], Set[str]] = {}
    for result in results:
        inv = result.inv
        seen.setdefault((inv.gate, inv.row_event), set()).add(result.verdict)
        if sets.get(inv.fixture_set, {}).get("kind") == "scenario":
            expected = inv.record.get("expect")
            if result.verdict != expected:
                errors.append(f"SCENARIO {inv.gate} {inv.row_event} {inv.fixture_key}: expected "
                              f"{expected}, got {result.verdict} ({inv.record.get('why', '')[:90]})")
    for gate, entry in sorted(manifest["hooks"].items()):
        if entry.get("role") != "gate":
            continue
        for wiring in entry.get("wirings", []):
            if wiring.get("no_replay"):
                continue
            matcher = wiring.get("matcher", "")
            row_event = wiring["event"] if not matcher else f"{wiring['event']}[{matcher}]"
            verdicts = seen.get((gate, row_event), set())
            if verdicts and verdicts <= {"allow"} and not wiring.get("allow_only"):
                errors.append(f"{gate} {row_event}: every replayed verdict is allow; add a scenario "
                              "on its deny or reopen path, or an allow_only reason")
            if wiring.get("allow_only") and verdicts - {"allow"}:
                errors.append(f"{gate} {row_event}: declared allow_only, but it reached "
                              f"{', '.join(sorted(verdicts - {'allow'}))}")
    return errors


def read_snapshot(path: Path = SNAPSHOT) -> List[Tuple[str, ...]]:
    rows: List[Tuple[str, ...]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                rows.append(tuple(line.split("\t")))
    except OSError:
        pass
    return rows


COUNTS = "(all)"


def snapshot_rows(results: List[Result]) -> List[Tuple[str, ...]]:
    """The committed form: one row per NON-allow verdict, plus one counts row
    per (gate, event). A fixture with no row was allowed; the counts row makes
    a fixture that vanished, or a new one that was allowed, visible too.

    Jev picked this over one row per invocation (0.55 against 0.38) as the
    lower-noise diff for a reviewer: about 250 lines instead of about 6,700,
    and a verdict flip still reads as exactly one changed row."""
    rows: List[Tuple[str, ...]] = []
    counts: Dict[Tuple[str, str], Dict[str, int]] = {}
    for result in results:
        gate, event, fixture, verdict, reason = result.row
        tally = counts.setdefault((gate, event), {})
        tally[verdict] = tally.get(verdict, 0) + 1
        if verdict != "allow":
            rows.append((gate, event, fixture, verdict, reason))
    for (gate, event), tally in counts.items():
        summary = " ".join(f"{v}={tally[v]}" for v in VERDICTS if v in tally)
        rows.append((gate, event, COUNTS, "counts", summary))
    return rows


def render_snapshot(rows: List[Tuple[str, ...]]) -> str:
    return SNAPSHOT_HEADER + "".join("\t".join(r) + "\n" for r in sorted(rows))


def diff_rows(old: List[Tuple[str, ...]], new: List[Tuple[str, ...]]) -> List[str]:
    """Readable per-(gate, event, fixture) changes. A fixture row missing on
    one side was an allow there, so a flip reads `deny -> allow`."""
    before = {r[:3]: r[3:] for r in old}
    after = {r[:3]: r[3:] for r in new}
    allowed = ("allow", "-")
    lines = []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if key[2] != COUNTS:
            a, b = a or allowed, b or allowed
        if a == b:
            continue
        label = "\t".join(key)
        if a is None:
            lines.append(f"+ {label}\t{' '.join(b or ())}")
        elif b is None:
            lines.append(f"- {label}\t{' '.join(a)}")
        elif key[2] == COUNTS:
            lines.append(f"~ {label}\t{a[1] if len(a) > 1 else ''} -> {b[1] if len(b) > 1 else ''}")
        else:
            lines.append(f"~ {label}\t{a[0]} -> {b[0]}\t{b[1] if len(b) > 1 else ''}")
    return lines


def summarise(manifest: Dict[str, Any], results: List[Result]) -> Dict[str, Any]:
    sets = manifest["fixture_sets"]
    real, synthetic, none = [], [], []
    for gate, entry in sorted(manifest["hooks"].items()):
        if entry.get("role") != "gate":
            continue
        for wiring in entry.get("wirings", []):
            label = f"{gate} {wiring['event']}"
            if wiring.get("no_replay"):
                none.append((label, wiring["no_replay"]))
            elif any(sets[s]["origin"] == "real" for s in wiring["fixtures"]):
                real.append(label)
            else:
                synthetic.append(label)
    verdicts: Dict[str, int] = {}
    for result in results:
        verdicts[result.verdict] = verdicts.get(result.verdict, 0) + 1
    return {"real": real, "synthetic": synthetic, "none": none, "verdicts": verdicts}


LOCKED_IMPORTS = ("psycopg",)


def missing_dependencies() -> List[str]:
    import importlib.util
    return [name for name in LOCKED_IMPORTS if importlib.util.find_spec(name) is None]


def run_only(manifest: Dict[str, Any], args: argparse.Namespace) -> int:
    """Replay a few gates and print every non-allow verdict and each scenario
    outcome. For writing scenarios; CI never runs this path."""
    wanted = {g.strip() for g in args.only.split(",") if g.strip()}
    manifest = json.loads(json.dumps(manifest))
    manifest["hooks"] = {k: v for k, v in manifest["hooks"].items()
                         if k in wanted or v.get("role") != "gate"}
    if args.scenarios:
        manifest["fixture_sets"]["scenarios"]["file"] = str(Path(args.scenarios).resolve())
    fixtures = load_fixtures(manifest)
    report = replay(manifest, fixtures, args.jobs, keep=args.keep_sandbox, progress=False)
    counts: Dict[Tuple[str, str], Dict[str, int]] = {}
    for result in report.results:
        key = (result.inv.gate, result.inv.row_event)
        counts.setdefault(key, {})
        counts[key][result.verdict] = counts[key].get(result.verdict, 0) + 1
        if result.verdict != "allow" or result.inv.fixture_set == "scenarios":
            print("\t".join(result.row)[:260])
            if result.verdict == "error":
                print("    " + " | ".join(result.detail.strip().splitlines()[-4:])[:400])
    for (gate, event), c in sorted(counts.items()):
        print(f"(all) {gate} {event}: " + " ".join(f"{k}={v}" for k, v in sorted(c.items())))
    errors = [e for e in behaviour_errors(manifest, report.results) if e.startswith("SCENARIO")]
    for line in errors:
        print(line)
    return 1 if errors or any(r.verdict == "error" for r in report.results) else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--update", action="store_true", help="rewrite the verdict snapshot")
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--keep-sandbox", action="store_true")
    parser.add_argument("--timing", action="store_true", help="print the slowest wirings")
    parser.add_argument("--report", help="also write every result, with crash detail, as JSON lines here")
    parser.add_argument("--only", help="DIAGNOSTIC: replay only these comma-separated gates and print "
                        "their verdicts; skips coverage, helper and snapshot checks, never writes")
    parser.add_argument("--scenarios", help="DIAGNOSTIC (with --only): read the scenario set from this file")
    args = parser.parse_args(argv)

    failures: List[str] = []
    manifest = load_manifest()
    if args.only:
        return run_only(manifest, args)
    names = bdp.roster()
    print("gate-replay: leak scan against the shape patterns"
          + (f" and the {names.describe()}" if names is not None else ""))
    leaks = leak_scan(leak_scan_targets(manifest_data=manifest))
    for finding in leaks:
        failures.append(f"LEAK {finding}")
    fixtures = load_fixtures(manifest)
    failures.extend(check_manifest(manifest, fixtures, REPO / "hooks", config_wirings()))
    if failures:
        print("gate-replay: FAIL before replay")
        for line in failures:
            print(f"  {line}")
        return 1

    missing = missing_dependencies()
    if missing:
        # The gates run under this interpreter, and several take a different
        # path when a locked dependency is absent, so the snapshot is only
        # meaningful on an interpreter with requirements.lock installed. A
        # hosted runner always has it; there, a missing module is a failure,
        # never a skip.
        message = (f"gate-replay: this interpreter ({sys.executable}) lacks "
                   f"{', '.join(missing)} from requirements.lock")
        if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
            print(message)
            return 1
        print(message + " — NOT CONFIGURED here; run it with the repo's .venv/bin/python")
        return 78

    try:
        report = replay(manifest, fixtures, args.jobs, keep=args.keep_sandbox)
    except WorkdirRefused as err:
        print(f"gate-replay: FAIL — {err}")
        return 1
    results = report.results
    errors = [r for r in results if r.verdict == "error"]
    for result in errors[:25]:
        failures.append(f"CRASH {result.inv.gate} {result.inv.row_event} {result.inv.fixture_key}: "
                        + " | ".join(result.detail.strip().splitlines()[-3:]))
    if len(errors) > 25:
        failures.append(f"... and {len(errors) - 25} more crashes")
    for line in report.mutated:
        failures.append(f"a gate wrote tracked or new content outside out/ during replay: {line}")
    failures.extend(helper_errors(manifest, results))
    failures.extend(behaviour_errors(manifest, results))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps({"row": result.row, "detail": result.detail,
                                         "seconds": round(result.seconds, 3),
                                         "opened": sorted(result.opened)}) + "\n")
    summary = summarise(manifest, results)
    rows = snapshot_rows(results)
    old = read_snapshot()
    changes = diff_rows(old, rows)

    print(f"gate-replay: {len(results)} invocations in {report.seconds:.1f}s "
          f"(sandbox {report.sandbox_seconds:.1f}s, {args.jobs} workers)")
    print(f"  wirings replayed on real fixtures: {len(summary['real'])}")
    print(f"  wirings replayed on synthetic fixtures only: {len(summary['synthetic'])}"
          + (f" ({', '.join(summary['synthetic'])})" if summary['synthetic'] else ""))
    print(f"  wirings with no replayable record: {len(summary['none'])}")
    for label, reason in summary["none"]:
        print(f"    {label}: {reason[:110]}...")
    print("  verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(summary["verdicts"].items())))
    if args.timing:
        per: Dict[str, float] = {}
        for result in results:
            key = f"{result.inv.gate} {result.inv.event}"
            per[key] = per.get(key, 0.0) + result.seconds
        for key, secs in sorted(per.items(), key=lambda kv: -kv[1])[:15]:
            print(f"    {secs:7.1f}s  {key}")

    if args.update:
        if errors or report.mutated:
            print("gate-replay: refusing to write a snapshot while a gate crashes or mutates the tree")
            for line in failures:
                print(f"  {line}")
            return 1
        SNAPSHOT.write_text(render_snapshot(rows), encoding="utf-8")
        print(f"gate-replay: wrote {SNAPSHOT.relative_to(REPO)} ({len(rows)} rows, {len(changes)} changed)")
        for line in changes[:60]:
            print(f"  {line}")
        rescan = leak_scan([SNAPSHOT])
        for finding in rescan:
            print(f"  LEAK {finding}")
        return 1 if (rescan or failures) else 0

    if changes:
        failures.append(f"{len(changes)} verdict(s) differ from {SNAPSHOT.relative_to(REPO)}; "
                        "if the change is deliberate, run `python3 ops/gate-replay.py --update` "
                        "and commit the snapshot with it")
    if failures:
        print("gate-replay: FAIL")
        for line in failures:
            print(f"  {line}")
        for line in changes[:80]:
            print(f"    {line}")
        if len(changes) > 80:
            print(f"    ... {len(changes) - 80} more")
        return 1
    print("gate-replay: OK — every verdict matches the snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
