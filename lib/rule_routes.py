"""Historical route-map audits plus context dedupe and full-text fitting.

Production selection lives in rule_scope_lookup. Map and class audits remain
until their separate map-removal slice migrates the remaining readers.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path


ROUTES_RELATIVE = "ops/config/rule-routes.v1.json"
ROUTES_SCHEMA = "rule-routes/v1"
# THE ONE LINE TO CHANGE when Builder A's ops/config/rule-classes.v1.json lands:
# rule_classes() reads this file and nothing else reads the classes.
CLASSES_RELATIVE = "ops/config/rule-classification.v1.csv"
CORPUS_RELATIVE = "ops/config/rule-selection-corpus.v1.json"
HOOKS_RELATIVE = "ops/config/hooks.json"
DELIVERING_HOOK = "hooks/rule-pack-preuse-reselection.py"

ROUTE_KINDS = frozenset({"boot", "trigger", "path_rule", "gate", "duplicate"})
TRIGGER_KEYS = ("tools", "verbs", "bash_patterns")
ENTRY_KEYS = frozenset({"moment", "routes", "no_trigger_reason", "note"})
# Tool names a route may name. Built-ins are Claude Code's own tools; the
# connector entries are the bare tool names a connector publishes, matched as
# mcp__<any server>__<name> because the server segment is an install-specific id.
KNOWN_BUILTIN_TOOLS = frozenset({
    "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Agent", "Task",
    "WebFetch", "WebSearch", "Skill", "Artifact", "AskUserQuestion", "SendMessage",
    "PushNotification", "Grep", "Glob", "functions.exec", "EnterPlanMode", "UpdatePlan",
    "update_plan", "functions.update_plan",
})
KNOWN_CONNECTOR_TOOLS = frozenset({
    # mail connector
    "send_message", "reply", "forward", "create_draft", "update_draft",
    "search_threads", "get_thread", "get_message", "list_drafts", "get_draft",
    # browser connectors
    "navigate", "get_page_text", "read_page", "computer", "find", "form_input",
    # scheduled tasks connector
    "create_scheduled_task", "update_scheduled_task",
    # Claude Code Remote connector: the two calls that launch cloud work, where
    # rule ede4b241 (cloud model choice) is put in front of the session
    "create_session", "create_trigger",
})
CONNECTOR_GLOB = re.compile(r"^mcp__(\*|[A-Za-z0-9_-]+)__([A-Za-z0-9_-]+)$")

REGISTRY_IMPORT = re.compile(r'from\s+"\./(scac-mutation-registry\.(?:v\d+|current)\.generated\.js)"')
REGISTRY_VERB = re.compile(r'"ingress_key":\s*"mcp-tool:([a-z0-9][a-z0-9-]*)"')
SHORT_ID = re.compile(r"\b[0-9a-f]{8}\b")

DEDUPE_SECONDS = 30 * 60
# Claude Code 2.1.283 persists any hook additionalContext longer than 10,000
# characters to a file and shows only a 2,000-character preview (CLo=1e4 in the
# shipped bundle, applied to every hook event). A preview is not delivery, so
# the receipt is fitted under the cap with headroom for the JSON envelope.
CONTEXT_CAP_CHARS = 10_000
CONTEXT_BUDGET_CHARS = 9_600
SUMMARY_CHARS = 100

# ------------------------------------------------------------------ inputs

def rule_classes(repo: Path, relative: str = CLASSES_RELATIVE) -> dict[str, dict]:
    """{id: {"class", "layer", "moment"}} — the reviewed classification.

    Reads the committed CSV today. A JSON file keyed by id (bare, or under a
    "rules" key) with the same fields is read the same way, so pointing
    CLASSES_RELATIVE at Builder A's rule-classes.v1.json is the whole switch."""
    path = Path(repo) / relative
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data.get("rules", data) if isinstance(data, dict) else {}
        rows = [{"id": rid, **row} for rid, row in data.items()
                if isinstance(row, dict) and len(rid) == 8]
    else:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    out = {}
    for row in rows:
        rid = (row.get("id") or "").strip()
        if len(rid) != 8:
            raise ValueError(f"{CLASSES_RELATIVE}: malformed id {rid!r}")
        out[rid] = {"class": (row.get("class") or "").strip(),
                    "layer": (row.get("layer") or "").strip(),
                    "moment": (row.get("moment") or "").strip()}
    return out

def corpus_ids(repo: Path) -> list[str]:
    data = json.loads((Path(repo) / CORPUS_RELATIVE).read_text(encoding="utf-8"))
    return sorted(row["id"] for row in data["rules"])

class RouteFileError(ValueError):
    """The route file cannot be used. `reason` is a fixed category (never the
    exception text, which may carry a local path): missing, unreadable,
    invalid_json, wrong_schema, no_rules or malformed_entry."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

def load_routes(repo: Path) -> dict:
    """The committed route file, structurally checked. Raises RouteFileError."""
    path = Path(repo) / ROUTES_RELATIVE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RouteFileError("missing") from None
    except (OSError, ValueError):
        raise RouteFileError("unreadable") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise RouteFileError("invalid_json") from None
    if not isinstance(data, dict) or data.get("schema") != ROUTES_SCHEMA:
        raise RouteFileError("wrong_schema")
    rules = data.get("rules")
    if not isinstance(rules, dict) or not rules:
        raise RouteFileError("no_rules")
    for entry in rules.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("routes"), list):
            raise RouteFileError("malformed_entry")
    return data


def known_verbs(repo: Path) -> set[str]:
    """Every verb the server serves, from the registry the server itself checks.

    mcp-server/src/tools.js assembles its verbs from many modules, several of
    which build entries programmatically, so no regex over tools.js finds them
    all. Every served verb must instead be registered, and
    mcp-server/src/mutation-registry.js imports exactly one generated registry
    whose `mcp-tool:<verb>` ingress keys are that set (reads included). This
    reads the generated file that import names, so a new registry successor is
    picked up without editing this file."""
    src = Path(repo) / "mcp-server/src"
    importer = (src / "mutation-registry.js").read_text(encoding="utf-8")
    names = REGISTRY_IMPORT.findall(importer)
    if len(set(names)) != 1:
        raise ValueError("mutation-registry.js does not import exactly one generated registry")
    text = (src / names[0]).read_text(encoding="utf-8")
    found = set(REGISTRY_VERB.findall(text))
    if not found:
        raise ValueError(f"{names[0]} declares no mcp-tool ingress")
    return found

def hook_matcher(repo: Path) -> str | None:
    """The PreToolUse matcher the delivering hook is registered under."""
    data = json.loads((Path(repo) / HOOKS_RELATIVE).read_text(encoding="utf-8"))
    for group in data.get("PreToolUse", []):
        for hook in group.get("hooks", []):
            if DELIVERING_HOOK in hook.get("command", ""):
                return group.get("matcher")
    return None

def stop_hooks(repo: Path) -> set[str]:
    """Basenames of every hook registered on Stop."""
    data = json.loads((Path(repo) / HOOKS_RELATIVE).read_text(encoding="utf-8"))
    names: set[str] = set()
    for group in data.get("Stop", []):
        for hook in group.get("hooks", []):
            names.update(re.findall(r"hooks/([\w.-]+\.py)", hook.get("command", "")))
    names.discard("hook-meter-run.py")
    return names

def tool_admitted(tool: str, matcher: str | None) -> bool:
    """Would the delivering hook be invoked at all for this tool (pattern)?"""
    if not matcher:
        return False
    probe = tool.replace("*", "server") if tool.startswith("mcp__") else tool
    return re.fullmatch(f"(?:{matcher})", probe) is not None

def tool_known(tool: str) -> bool:
    if tool in KNOWN_BUILTIN_TOOLS:
        return True
    match = CONNECTOR_GLOB.match(tool)
    return bool(match and match.group(2) in KNOWN_CONNECTOR_TOOLS)

# ------------------------------------------------------------------ matching

# ------------------------------------------------------------------ dedupe

def _dedupe_root() -> Path:
    return Path(os.environ.get(
        "CARR_RULE_ROUTE_DEDUPE_DIR",
        Path.home() / ".config/carr/claude-rule-delivery/routes"))

def _dedupe_path(session_id: str, agent_id: str | None = None) -> Path:
    """One state file per CONTEXT: the main conversation keeps its historical
    name; a subagent (agent_id) gets its own, because a rule the main context
    holds is not in the child's window and vice versa."""
    key = session_id if not agent_id else f"{session_id}\x00agent:{agent_id}"
    token = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _dedupe_root() / f"{token}.json"

def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def _context_epoch(session_id: str, agent_id: str | None = None) -> str | None:
    """The rule-boot gate's epoch for this CONTEXT, or None when unknown.

    The boot gate rotates the main arm epoch at every SessionStart (compaction,
    resume, clear): exactly the moments a context stops holding what it was
    given. A subagent context has its own epoch, written when that child
    booted, which the main arm's rotations do not touch and which the child's
    own compaction rotates. Dedupe scoped to the context epoch therefore means
    "delivered to THIS context", never "delivered somewhere in this session".
    """
    try:
        from lib import rule_boot_gate
        if agent_id:
            epoch = rule_boot_gate._read_agent_epoch(session_id, agent_id)
            return str(epoch) if epoch else None
        arm = rule_boot_gate.read_arm(session_id) or {}
        epoch = arm.get("epoch")
        return str(epoch) if epoch else None
    except Exception:
        return None

def _seen_in_context(state: dict, rid: str, epoch: str | None, now: float) -> bool:
    """Whether rid's full text already reached this context.

    With an epoch: any tool's delivery in this epoch counts (the text sits in
    the same context whichever tool it arrived with). Without one: the older
    per-tool-agnostic time window, so a machine with no boot state still
    stops repeating a rule every few minutes.
    """
    tools = state.get(rid) or {}
    if not isinstance(tools, dict):
        return False
    if epoch is not None and state.get("_epoch") == epoch:
        return any(isinstance(ts, (int, float)) for ts in tools.values())
    if epoch is not None:
        return False  # a different context: nothing it holds is this one's
    return any(isinstance(ts, (int, float)) and now - ts < DEDUPE_SECONDS
               for ts in tools.values())

def fresh_ids(session_id: str, tool_name: str, ids: list[str],
              now: float | None = None, *, agent_id: str | None = None, corpus_digest: str | None = None) -> list[str]:
    """The ids whose full text this context has not already been given.

    2026-10-12: scoped to the context (the boot gate's arm epoch) and across
    tools. The 30-minute per-tool window re-sent the same rule to one session
    five times in two hours (57K of 137K rule characters were repeats); a rule
    delivered for a Bash call is in context for the Write call that follows.

    agent_id names a subagent context; its deliveries and epoch are its own
    (reviewer finding on PR 1708: keyed by the main epoch alone, a rule the
    main context held was withheld from every child, and a child's from main).

    FAILS OPEN: any error reading the state delivers everything, because a
    duplicate injection costs context while a dropped one costs the rule."""
    now = time.time() if now is None else now
    try:
        state = _read_state(_dedupe_path(session_id, agent_id))
        if corpus_digest is not None and state.get("_corpus_digest") != corpus_digest:
            return sorted(ids)
        epoch = _context_epoch(session_id, agent_id)
    except Exception:
        return sorted(ids)
    return sorted(rid for rid in ids if not _seen_in_context(state, rid, epoch, now))

def record_delivered(session_id: str, tool_name: str, ids: list[str],
                     now: float | None = None, *, agent_id: str | None = None, corpus_digest: str | None = None) -> None:
    """Remember full-text deliveries for one context. Best effort; never raises."""
    now = time.time() if now is None else now
    try:
        path = _dedupe_path(session_id, agent_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = path.with_suffix(".lock")
        with open(lock, "a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            state = _read_state(path)
            epoch = _context_epoch(session_id, agent_id)
            if ((epoch is not None and state.get("_epoch") != epoch)
                    or (corpus_digest is not None and state.get("_corpus_digest") != corpus_digest)):
                state = {"_epoch": epoch}  # a new context holds none of the old deliveries
            if corpus_digest is not None:
                state["_corpus_digest"] = corpus_digest
            for rid in ids:
                state.setdefault(rid, {})[tool_name] = now
            # Forget anything older than the window so the file stays small
            # (an epoch-scoped context keeps everything it was given).
            for rid in list(state):
                if rid in {"_epoch", "_corpus_digest"}:
                    continue
                if not isinstance(state[rid], dict):
                    del state[rid]
                    continue
                tools = {t: ts for t, ts in state[rid].items()
                         if isinstance(ts, (int, float))
                         and (epoch is not None or now - ts < DEDUPE_SECONDS)}
                if tools:
                    state[rid] = tools
                else:
                    del state[rid]
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            os.chmod(temp, 0o600)
            os.replace(temp, path)
    except Exception:
        return

# ------------------------------------------------------------------ fitting


def one_line(statement: str, limit: int = SUMMARY_CHARS) -> str:
    text = " ".join((statement or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"

def context_chars(text: str) -> int:
    """Length as the harness measures it (a JavaScript string, UTF-16 units)."""
    return len(text.encode("utf-16-le")) // 2

def fit_rules(rules: list[dict], render, *, always_on: set[str],
              budget: int = CONTEXT_BUDGET_CHARS) -> tuple[list[dict], list[dict]]:
    """Split rules into (full, overflow) so render(full, overflow) fits budget.

    Every rule starts as a one-line overflow entry; rules are then promoted to
    full text in priority order — those NOT already in the always-on file
    first, then the rest — whenever the promotion still fits. Nothing is ever
    dropped: a rule that does not fit keeps its id and one-line summary."""
    order = sorted(rules, key=lambda r: (r["id"] in always_on, r["id"]))
    full: list[dict] = []
    overflow = [{"id": r["id"], "summary": one_line(r["statement"])} for r in order]
    if context_chars(render(full, overflow)) > budget:
        overflow = [{"id": r["id"], "summary": ""} for r in order]
    for rule in order:
        trial_full = full + [rule]
        trial_over = [o for o in overflow if o["id"] != rule["id"]]
        if context_chars(render(trial_full, trial_over)) <= budget:
            full, overflow = trial_full, trial_over
    return full, overflow

def within_cap(text: str, budget: int = CONTEXT_BUDGET_CHARS) -> bool:
    return context_chars(text) <= budget

# ------------------------------------------------------------------ receipt
