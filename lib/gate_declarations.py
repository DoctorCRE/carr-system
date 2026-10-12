"""Gate wiring, replay scenarios and selftest pairing from one declaration."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT = REPO / "ops/config/gate-declarations.json"
_GATE = re.compile(r"hooks/([A-Za-z0-9_.-]+\.py)")
_EVENT = re.compile(r"\bCARR_CONTEXT_HOOK_EVENT=(\w+)")

# A CHAIN (WR-000259) runs several gates of one event and matcher in ONE
# interpreter: lib/hook_execution.py run_chain. Executions that carry the same
# "chain" name and sit at consecutive orders of one group render as a single
# hooks.json command; every member keeps its own declaration, solo command and
# timeout, so the replay, pairing and slot checks below are unchanged.
SOLO_PREFIX = "{{REPO}}/.venv/bin/python {{REPO}}/hooks/hook-meter-run.py "
CHAIN_FLAG = "--chain"
CHAIN_EVENTS = frozenset({"PreToolUse", "PostToolUse"})
CHAIN_SLACK_SECONDS = 5      # outer budget above the members' summed timeouts
DEFAULT_TIMEOUT = 60         # Claude Code's own default for a command hook
_CHAIN_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
# A member shares one interpreter with the others, so anything that acts on the
# whole PROCESS (exiting it, its signals, threads, its working directory) or
# swallows the chain's per-member timeout is refused at admission.
_CHAIN_HAZARDS = re.compile(
    r"os\._exit|\bsignal\.(?:alarm|setitimer|signal)\b|\bthreading\b|os\.fork\b"
    r"|\batexit\b|os\.chdir\b|except\s*:|except\s+BaseException\b")


def chain_hazards(source_text):
    """Process-global constructs that make a gate unsafe to share an interpreter."""
    return sorted(set(_CHAIN_HAZARDS.findall(source_text)))


def chain_command(members):
    """The one hooks.json hook a run of chained members renders to."""
    tokens, total = [], 0
    for member in members:
        command = member.get("command", "")
        path = command[len(SOLO_PREFIX):] if command.startswith(SOLO_PREFIX) else ""
        if not path or " " in path or ":" in path:
            raise ValueError(f"chain member is not a plain solo command: {command!r}")
        timeout = member.get("timeout", DEFAULT_TIMEOUT)
        tokens.append(f"{timeout}:{path}")
        total += timeout
    return {"type": "command",
            "command": SOLO_PREFIX + CHAIN_FLAG + " " + " ".join(tokens),
            "timeout": total + CHAIN_SLACK_SECONDS}


def _fuse(hooks):
    """Replace each run of same-chain members with its one rendered command."""
    out = []
    for hook in hooks:
        name = hook.get("chain")
        if name and out and isinstance(out[-1], list) and out[-1][0].get("chain") == name:
            out[-1].append(hook)
        elif name:
            out.append([hook])
        else:
            out.append(hook)
    return [chain_command(item) if isinstance(item, list) else item for item in out]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate declaration key: {key}")
        result[key] = value
    return result


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_unique_object)


def _render(data, source):
    events = {}
    for entry in data["hooks"].values():
        for wiring in entry.get("wirings", []):
            for execution in wiring.get("executions", []):
                if execution["source"] != source:
                    continue
                event = events.setdefault(wiring["event"], {})
                group = event.setdefault(execution["group"], {})
                if execution["matcher_present"]:
                    group["matcher"] = wiring.get("matcher", "")
                group.setdefault("hooks", {})[execution["order"]] = {
                    k: v for k, v in execution.items()
                    if k not in {"source", "group", "order", "matcher_present"}
                }
    return {
        event: [{**{k: v for k, v in group.items() if k != "hooks"},
                 "hooks": _fuse([hook for _, hook in sorted(group["hooks"].items())])}
                for _, group in sorted(groups.items())]
        for event, groups in events.items()
    }


def render_hooks(path=DEFAULT, *, source="ops/config/hooks.json"):
    """Render a source's hooks block, preserving command, group and hook order."""
    data = _load(path)
    if source not in data["sources"]:
        raise ValueError(f"undeclared hook source: {source}")
    return _render(data, source)


def replay_hooks(path=DEFAULT):
    """The hook entries consumed by gate replay, without delivery metadata."""
    entries = copy.deepcopy(_load(path)["hooks"])
    for entry in entries.values():
        entry.pop("paired_selftest", None)
        entry.pop("unpaired_reason", None)
        for wiring in entry.get("wirings", []):
            wiring.pop("executions", None)
    return entries


def paired_selftest(name, path=DEFAULT):
    """The declared suite for a hook path; None means explicitly unpaired."""
    entry = _load(path)["hooks"].get(Path(name).name)
    if entry is None:
        raise ValueError(f"undeclared hook: {name}")
    return entry.get("paired_selftest")


def _command_gate(command):
    if "run-record-gate.py" in command:
        tail = command.split("run-record-gate.py", 1)[1].strip().split()
        return tail[0] if tail else None
    names = [name for name in _GATE.findall(command) if name != "hook-meter-run.py"]
    return names[0] if names else None


def _chain_member_errors(name, event, command, timeout, chain, repo):
    """Admission for one chained execution; see the chain note at the top."""
    errors = []
    if not isinstance(chain, str) or not _CHAIN_NAME.fullmatch(chain):
        return [f"{name}: invalid chain name"]
    if event not in CHAIN_EVENTS:
        errors.append(f"{name}: chain {chain} is on {event}; chains are admitted on "
                      + " and ".join(sorted(CHAIN_EVENTS)) + " only")
    if command != SOLO_PREFIX + "{{REPO}}/hooks/" + name:
        errors.append(f"{name}: chain member must be wired as the plain solo command")
    if timeout is not None and (not isinstance(timeout, int) or isinstance(timeout, bool)):
        errors.append(f"{name}: chain member timeout must be whole seconds")
    try:
        hazards = chain_hazards((Path(repo) / "hooks" / name).read_text(encoding="utf-8"))
    except OSError:
        hazards = []
    if hazards:
        errors.append(f"{name}: cannot share an interpreter in chain {chain}: "
                      + ", ".join(hazards))
    return errors


def validate(path=DEFAULT, *, repo=REPO):
    """Return declaration defects without executing gates or reading live state."""
    repo = Path(repo)
    try:
        data = _load(path)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    if not isinstance(data, dict):
        return ["gate declarations must be an object"]
    errors = []
    if data.get("schema") != "carr-gate-declarations/v1":
        errors.append("unsupported gate declaration schema")
    entries = data.get("hooks")
    sources = data.get("sources")
    if not isinstance(entries, dict) or not isinstance(sources, list):
        return errors + ["hooks must be an object and sources must be a list"]
    if not all(isinstance(source, str) and (
            source in {"ops/config/hooks.json", ".claude/settings.json"}
            or re.fullmatch(r"claude-tree/settings/[A-Za-z0-9_-]+\.settings\.json", source))
            for source in sources):
        return errors + ["execution sources must name repository hook configuration files"]
    if len(sources) != len(set(sources)):
        errors.append("duplicate hook source")
    disk = {p.name for p in (repo / "hooks").glob("*.py")}
    for name in sorted(disk - entries.keys()):
        errors.append(f"hooks/{name}: missing declaration")
    occupied, groups, chains = set(), {}, {}
    for name, entry in entries.items():
        if name not in disk:
            errors.append(f"hooks/{name}: declared hook absent")
        if not isinstance(entry, dict):
            errors.append(f"{name}: declaration must be an object")
            continue
        if entry.get("role") not in {"gate", "helper", "wrapper"}:
            errors.append(f"{name}: invalid role")
        pair = entry.get("paired_selftest")
        if pair and (not isinstance(pair, str) or not re.fullmatch(r"ops/[A-Za-z0-9_.-]+-selftest\.py", pair)
                     or not (repo / pair).is_file()):
            errors.append(f"{name}: invalid paired selftest")
        if entry.get("role") == "gate" and not pair and not entry.get("unpaired_reason"):
            errors.append(f"{name}: missing paired selftest or unpaired reason")
        wirings = entry.get("wirings", [])
        if entry.get("role") == "gate" and not wirings:
            errors.append(f"{name}: gate has no replay wiring")
        seen = set()
        for wiring in wirings:
            event, matcher = wiring.get("event"), wiring.get("matcher", "")
            env_event = (wiring.get("env") or {}).get("CARR_CONTEXT_HOOK_EVENT", "")
            key = (event, matcher, env_event)
            if key in seen:
                errors.append(f"{name}: duplicate replay wiring {event}")
            seen.add(key)
            if not isinstance(event, str) or not isinstance(matcher, str):
                errors.append(f"{name}: invalid replay event or matcher")
                continue
            no_replay = wiring.get("no_replay")
            if no_replay and (event != "SessionStart" or wiring.get("fixtures")):
                errors.append(f"{name}: invalid no_replay exception")
            if not no_replay and not wiring.get("fixtures"):
                errors.append(f"{name}: replay wiring has no scenarios")
            executions = wiring.get("executions", [])
            if not executions:
                errors.append(f"{name}: replay wiring has no execution {event}")
            for execution in executions:
                source = execution.get("source")
                command = execution.get("command", "")
                group, order = execution.get("group"), execution.get("order")
                if source not in sources:
                    errors.append(f"{name}: undeclared execution source")
                if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (group, order)):
                    errors.append(f"{name}: invalid execution group or order")
                    continue
                slot = (source, event, group, order)
                if slot in occupied:
                    errors.append(f"{name}: duplicate execution slot")
                occupied.add(slot)
                shape = (matcher, execution.get("matcher_present"))
                group_key = (source, event, group)
                if group_key in groups and groups[group_key] != shape:
                    errors.append(f"{name}: execution group matcher mismatch")
                groups[group_key] = shape
                if execution.get("type") != "command" or not isinstance(command, str) or _command_gate(command) != name:
                    errors.append(f"{name}: execution command names a different hook")
                actual_env = _EVENT.search(command) if isinstance(command, str) else None
                if (actual_env.group(1) if actual_env else "") != env_event:
                    errors.append(f"{name}: execution event mismatch")
                if env_event and env_event != event:
                    errors.append(f"{name}: replay event differs from wired event")
                timeout = execution.get("timeout")
                if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0):
                    errors.append(f"{name}: invalid execution timeout")
                chain = execution.get("chain")
                if chain is not None:
                    errors.extend(_chain_member_errors(name, event, command, timeout, chain, repo))
                    chains.setdefault((source, event, str(chain)), []).append((group, order))
    for (source, event, chain), slots in sorted(chains.items()):
        orders = sorted(order for _, order in slots)
        if len(slots) < 2:
            errors.append(f"chain {chain}: a chain needs at least two members")
        if (len({group for group, _ in slots}) != 1
                or orders != list(range(orders[0], orders[0] + len(orders)))):
            errors.append(f"chain {chain}: members must sit at consecutive orders of one {event} group")
    for source, event, group in groups:
        orders = sorted(slot[3] for slot in occupied if slot[:3] == (source, event, group))
        if orders != list(range(len(orders))):
            errors.append(f"{source} {event}: execution orders must be contiguous")
    for source, event in {(source, event) for source, event, _ in groups}:
        indexes = sorted(group for src, evt, group in groups if (src, evt) == (source, event))
        if indexes != list(range(len(indexes))):
            errors.append(f"{source} {event}: execution groups must be contiguous")
    return errors


def check(path=DEFAULT, *, repo=REPO):
    """Validate declarations and compare every tracked delivery projection."""
    errors = validate(path, repo=repo)
    if errors:
        return errors
    data = _load(path)
    for source in data["sources"]:
        try:
            expected = _load(Path(repo) / source)
            expected = expected.get("hooks", expected)
            if _render(data, source) != expected:
                errors.append(f"{source}: execution projection mismatch")
        except (OSError, ValueError) as exc:
            errors.append(f"{source}: cannot verify projection: {exc}")
    try:
        scenario_sets = _load(Path(repo) / "ops/config/gate-replay-manifest.json")["fixture_sets"]
        for name, entry in data["hooks"].items():
            for wiring in entry.get("wirings", []):
                for fixture in wiring.get("fixtures", []):
                    if fixture not in scenario_sets:
                        errors.append(f"{name}: unknown fixture set {fixture}")
    except (OSError, ValueError, KeyError) as exc:
        errors.append(f"cannot verify replay scenario coverage: {exc}")
    return errors


def write(path=DEFAULT, *, repo=REPO):
    """Regenerate declared hooks blocks after validating and reading all sources."""
    errors = validate(path, repo=repo)
    if errors:
        return errors
    data = _load(path)
    pending = []
    for source in data["sources"]:
        target = Path(repo) / source
        try:
            if not target.resolve().is_relative_to(Path(repo).resolve()):
                errors.append(f"{source}: hook configuration is outside the repository")
                continue
            existing = _load(target)
            if not isinstance(existing, dict):
                errors.append(f"{source}: hook configuration must be an object")
                continue
            hooks = _render(data, source)
            if source == "ops/config/hooks.json":
                projected = hooks
            else:
                projected = {**existing, "hooks": hooks}
            pending.append((target, json.dumps(projected, indent=2) + "\n"))
        except (OSError, ValueError) as exc:
            errors.append(f"{source}: cannot read hook configuration before write: {exc}")
    if errors:
        return errors
    for target, content in pending:
        try:
            if target.read_text(encoding="utf-8") != content:
                target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return [f"{target.relative_to(repo)}: cannot write hook projection: {exc}"]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT)
    parser.add_argument("--repo", type=Path, default=REPO)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    action.add_argument("--paired")
    args = parser.parse_args(argv)
    if args.paired is not None:
        try:
            print(paired_selftest(args.paired, args.path) or "")
            return 0
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    errors = write(args.path, repo=args.repo) if args.write else check(args.path, repo=args.repo)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Gate declarations: wiring projections written" if args.write else
          "Gate declarations: wiring projections and selftest pairing verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
