"""Deterministic rule delivery routes: which rule text a tool call must carry.

THE DESIGN (Joe demands 100% recall; Jev chose it at p=1.00). Every active
rule has at least one DELIVERY ROUTE, recorded in ops/config/rule-routes.v1.json:

  boot       always-on text or an index line, composed at session start;
  trigger    full text injected by the existing PreToolUse rail
             (hooks/rule-pack-preuse-reselection.py) when the pending call
             matches: an exact tool name (or an mcp__*__name connector glob),
             a record verb name, a bash command pattern;
  path_rule  the same injection when a file path in the call matches a glob.
             Kept apart from `trigger` so the purely path-scoped rules can
             also be emitted as native path-scoped rule files later;
  gate       an installed code gate enforces it;
  duplicate  the rule's surviving duplicate carries the delivery.

NO SIMILARITY AND NO THRESHOLD ON THIS PATH. Every match is exact: a tool name
equality (or fnmatch over the connector's server segment), a verb name
equality, a regular expression over a Bash command, or an fnmatch over a path.
Semantic judgement stays where it already lives (the UserPromptSubmit rail).

A LIBRARY, not a script: no shebang and no entry-point guard, for the sealed
source inventory reason ops/typesafe_client.py documents.
"""
from __future__ import annotations

import csv
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import time
import unicodedata
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
PATH_INPUT_KEYS = ("file_path", "path", "notebook_path")
# Built-in tools that only look. A path_rule route is a write-moment route (the
# moments name building, editing and committing), so a call that merely reads a
# matching path is not that moment. evals/rule-delivery measured routine reads
# receiving rules through these globs. Every other tool keeps matching.
READ_ONLY_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})
BASH_TOOLS = frozenset({"Bash", "functions.exec"})

# Tool names a route may name. Built-ins are Claude Code's own tools; the
# connector entries are the bare tool names a connector publishes, matched as
# mcp__<any server>__<name> because the server segment is an install-specific id.
KNOWN_BUILTIN_TOOLS = frozenset({
    "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Agent", "Task",
    "WebFetch", "WebSearch", "Skill", "Artifact", "AskUserQuestion", "SendMessage",
    "PushNotification", "Grep", "Glob", "functions.exec",
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

RUN_SH_CALL = re.compile(r"\brun\.sh\s+call\s+['\"]?([a-z0-9][a-z0-9-]*)", re.I)
CALL_VERB_PY = re.compile(r"\bcall-verb\.py\s+['\"]?([a-z0-9][a-z0-9-]*)", re.I)
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
ALWAYS_ON_DEFAULT = "~/.claude/rules/carr-rules-always-on.md"
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


def routes_digest(repo: Path) -> str:
    return hashlib.sha256((Path(repo) / ROUTES_RELATIVE).read_bytes()).hexdigest()


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

def call_verbs(tool_name: str, tool_input: object) -> set[str]:
    """Every record verb this call reaches: the MCP tool itself, the call-verb
    passthrough's inner verb, or the Bash door (`run.sh call <verb>`)."""
    verbs: set[str] = set()
    if tool_name.startswith("mcp__") and "__" in tool_name[5:]:
        verb = tool_name.rsplit("__", 1)[1]
        verbs.add(verb)
        if tool_name.startswith(("mcp__carr__", "mcp__carr_records__")):
            verbs.add(verb.replace("_", "-"))
        if verb.replace("_", "-") == "call-verb" and isinstance(tool_input, dict):
            inner = tool_input.get("verb")
            if isinstance(inner, str) and inner.strip():
                verbs.add(inner.strip())
    if tool_name in BASH_TOOLS and isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            verbs.update(m.group(1).lower() for m in RUN_SH_CALL.finditer(command))
            verbs.update(m.group(1).lower() for m in CALL_VERB_PY.finditer(command))
    return verbs


def _trim_patch_space(value: str) -> str:
    """Match the patch tool's Unicode White_Space trim, excluding Python's FS–US."""
    def is_space(char: str) -> bool:
        return char in "\t\n\v\f\r\x85" or unicodedata.category(char) in {"Zs", "Zl", "Zp"}

    start, end = 0, len(value)
    while start < end and is_space(value[start]):
        start += 1
    while end > start and is_space(value[end - 1]):
        end -= 1
    return value[start:end]


def call_paths(tool_input: object) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    paths = [tool_input[key] for key in PATH_INPUT_KEYS
             if isinstance(tool_input.get(key), str) and tool_input[key].strip()]
    # Codex's canonical apply_patch input has one command string rather than
    # Claude's file_path. The patch headers are the paths the tool will touch.
    command = tool_input.get("command")
    if not isinstance(command, str):
        return paths
    # apply_patch accepts surrounding blank space and CRLF envelopes. Parse
    # the same normalized boundary rather than rejecting a valid patch.
    command = _trim_patch_space(command.replace("\r\n", "\n"))
    # The patch tool trims Unicode whitespace on each marker line. Inspect only
    # the first line so a prose or heredoc wrapper cannot expose inner headers.
    marker = _trim_patch_space(command.partition("\n")[0])
    if marker == "*** Begin Patch" and "\n" in command:
        paths.extend(match.group(1).strip() for match in re.finditer(
            r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", command, re.M))
        paths.extend(match.group(1).strip() for match in re.finditer(
            r"^\*\*\* Move to: (.+)$", command, re.M))
    return paths


class RouteShapeError(ValueError):
    """One route cannot be evaluated (a non-string tool, verb, pattern or glob,
    or an empty glob). The coverage gate refuses these in CI; at run time the
    matcher treats the rule as matched, so a broken route over-delivers rather
    than silently dropping its rule."""


def _strings(route: dict, key: str) -> list[str]:
    values = route.get(key)
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise RouteShapeError(key)
    return values


def route_matches(route: dict, tool_name: str, tool_input: object,
                  verbs: set[str] | None = None) -> bool:
    """True when this route fires for the call. Raises RouteShapeError when the
    route itself is malformed."""
    kind = route.get("kind")
    if kind == "path_rule":
        globs = _strings(route, "path_globs")
        if not globs:
            raise RouteShapeError("path_globs")
        read_only = route.get("read_only", False)
        if not isinstance(read_only, bool):
            raise RouteShapeError("read_only")
        if tool_name in READ_ONLY_TOOLS and not read_only:
            return False
        paths = call_paths(tool_input)
        return any(fnmatch.fnmatch(path, pattern) for pattern in globs for path in paths)
    if kind != "trigger":
        return False
    tools = _strings(route, "tools")
    route_verbs = _strings(route, "verbs")
    patterns = _strings(route, "bash_patterns")
    names = {tool_name}
    if tool_name == "apply_patch":
        names.update(("Write", "Edit", "MultiEdit"))
    for tool in tools:
        if any(fnmatch.fnmatchcase(name, tool) for name in names):
            return True
    verbs = call_verbs(tool_name, tool_input) if verbs is None else verbs
    if verbs & set(route_verbs):
        return True
    if tool_name in BASH_TOOLS and isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            for pattern in patterns:
                try:
                    if re.search(pattern, command, re.I):
                        return True
                except re.error:
                    raise RouteShapeError("bash_patterns") from None
    return False


def matched_rule_ids(doc: dict, tool_name: str, tool_input: object) -> list[str]:
    """Every rule whose trigger or path route this exact call hits. A rule with
    a malformed route counts as hit: fail open means deliver, never drop."""
    verbs = call_verbs(tool_name, tool_input)
    hits = []
    for rid, entry in (doc.get("rules") or {}).items():
        routes = entry.get("routes") if isinstance(entry, dict) else None
        for route in routes or ():
            if not isinstance(route, dict):
                hits.append(rid)
                break
            try:
                hit = route_matches(route, tool_name, tool_input, verbs)
            except RouteShapeError:
                hit = True
            if hit:
                hits.append(rid)
                break
    return sorted(hits)


# ------------------------------------------------------------------ dedupe

def _dedupe_root() -> Path:
    return Path(os.environ.get(
        "CARR_RULE_ROUTE_DEDUPE_DIR",
        Path.home() / ".config/carr/claude-rule-delivery/routes"))


def _dedupe_path(session_id: str) -> Path:
    token = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return _dedupe_root() / f"{token}.json"


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def fresh_ids(session_id: str, tool_name: str, ids: list[str],
              now: float | None = None) -> list[str]:
    """The ids not delivered to this session for this same tool in the window.

    FAILS OPEN: any error reading the state delivers everything, because a
    duplicate injection costs context while a dropped one costs the rule."""
    now = time.time() if now is None else now
    try:
        state = _read_state(_dedupe_path(session_id))
    except Exception:
        return sorted(ids)
    out = []
    for rid in ids:
        seen = (state.get(rid) or {}).get(tool_name)
        if isinstance(seen, (int, float)) and now - seen < DEDUPE_SECONDS:
            continue
        out.append(rid)
    return sorted(out)


def record_delivered(session_id: str, tool_name: str, ids: list[str],
                     now: float | None = None) -> None:
    """Remember full-text deliveries. Best effort; never raises."""
    now = time.time() if now is None else now
    try:
        path = _dedupe_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = path.with_suffix(".lock")
        with open(lock, "a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            state = _read_state(path)
            for rid in ids:
                state.setdefault(rid, {})[tool_name] = now
            # Forget anything older than the window so the file stays small.
            for rid in list(state):
                tools = {t: ts for t, ts in state[rid].items()
                         if isinstance(ts, (int, float)) and now - ts < DEDUPE_SECONDS}
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

def always_on_ids(path: str | None = None) -> set[str]:
    """Rule ids already carried by Builder A's always-on file, when present."""
    target = Path(os.path.expanduser(
        path or os.environ.get("CARR_RULES_ALWAYS_ON_FILE", ALWAYS_ON_DEFAULT)))
    try:
        return set(SHORT_ID.findall(target.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # Unreadable or not UTF-8: ordering falls back to id order, and every
        # rule is still delivered. Never a crash after the door call is paid.
        return set()


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


# ------------------------------------------------------------------ notices
#
# Every way the route rail can fail to deliver ends in one of these fixed
# notices, never in silence and never in an exception's own text (which may
# carry a path or a credential). Each says, in this order: rules were NOT
# delivered, the call itself went ahead, what to do before acting, and the ids.
# Jev reviewed the wording (semantic_creation, 2026-09-26): every notice scored
# 0.94-0.95 on "the session knows the rules were NOT delivered" and 0.76-0.93
# on "the session knows what to do next".

CALL_WENT_AHEAD = "The tool call itself was not blocked or changed."


def _ids(ids) -> str:
    return ", ".join(sorted(set(ids))) + "."


def notice_door_failure(ids) -> str:
    return ("RULE ROUTE DELIVERY FAILED (selector_unavailable): the rules below bind this "
            "tool call and were NOT delivered; you have not seen them. " + CALL_WENT_AHEAD +
            " Before you act, fetch each one with the standing-context verb (rule_ids: the "
            "ids below) and follow it; if that also fails, say so and do not act as if the "
            "rules were read. Undelivered rule ids: " + _ids(ids))


def notice_file_unreadable(reason: str, ids) -> str:
    head = ("RULE ROUTE FILE UNREADABLE (" + reason + "): rules were NOT delivered. "
            + ROUTES_RELATIVE + ", which decides which rules bind each tool call, could not "
            "be read, so no routed rule was checked or shown for this call; you have not "
            "seen any of them. " + CALL_WENT_AHEAD + " ")
    if not ids:
        return head + ("Before you act, call the standing-context verb with no arguments, "
                       "read each rule whose subject covers this tool call and follow it, and "
                       "report the broken route file.")
    return head + ("Before you act, fetch the rules below that could apply to this action "
                   "with the standing-context verb (rule_ids), and report the broken route "
                   "file. Rules that may bind this call and were NOT delivered: " + _ids(ids))


def notice_error(ids=None) -> str:
    if ids:
        return ("RULE ROUTE DELIVERY ERROR (internal_error): the rule-delivery hook failed, "
                "and rules were NOT delivered for this call; you have not seen them. "
                + CALL_WENT_AHEAD + " Before you act, fetch these rules with the "
                "standing-context verb (rule_ids) and follow them: " + _ids(ids))
    return ("RULE ROUTE DELIVERY ERROR (internal_error): the rule-delivery hook failed "
            "before it could tell which rules bind this call, so NO rule was delivered; you "
            "have not seen them. " + CALL_WENT_AHEAD + " Before you act: (1) call the "
            "standing-context verb with no arguments, which returns every rule in scope; "
            "(2) read each rule whose subject covers this tool call and follow it; (3) if "
            "standing-context also fails, stop and tell the user the rules could not be read "
            "instead of acting from memory.")


def notice_too_large(ids) -> str:
    return ("RULE ROUTE DELIVERY TOO LARGE: " + str(len(set(ids))) + " rules bind this call, "
            "more than the hook's " + f"{CONTEXT_CAP_CHARS:,}" + "-character context cap can "
            "carry even as one-line summaries, so NONE was delivered; you have not seen them. "
            + CALL_WENT_AHEAD + " Before you act, fetch these rules with the standing-context "
            "verb (rule_ids) in batches and follow them: " + _ids(ids))


def within_cap(text: str, budget: int = CONTEXT_BUDGET_CHARS) -> bool:
    return context_chars(text) <= budget


# ------------------------------------------------------------------ receipt

ROUTE_RECEIPT_SCHEMA = "rule-route-trigger-delivery/v1"
ROUTE_RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "client", "session_id", "turn_id", "tool_use_id",
    "tool_name", "tool_input_sha256", "routes_digest", "triggers_digest",
    "map_digest", "source_digest", "trigger_ids", "route_rule_ids", "packs",
    "identity", "rule_ids", "rules", "overflow", "not_found", "instruction",
    "rule_delivery",
})
ROUTE_INSTRUCTION = (
    "Each rule in `rules` binds this tool call; read it before acting. A rule in "
    "`overflow` also binds but did not fit this hook's context cap: fetch its full "
    "text with standing-context rule_ids before acting on it. A rule in `not_found` "
    "was routed here but the rule store did not return it."
)


def validate_route_receipt(row: object, *, repo: Path) -> bool:
    """Shape, partition and digest checks for one route delivery receipt."""
    from lib.rule_delivery_preuse import (  # local: keeps this module import-light
        DELIVERY_KEYS, IDENTITY_KEYS, RULE_KEYS, TRIGGER_TABLE_RELATIVE, receipt_id,
        valid_local_identity)
    from lib.rule_delivery_shadow import file_sha256, source_sha256
    if not isinstance(row, dict) or set(row) != ROUTE_RECEIPT_KEYS:
        return False
    if row.get("schema") != ROUTE_RECEIPT_SCHEMA or row.get("client") not in {"claude", "codex"}:
        return False
    turn_id = row.get("turn_id")
    if (row["client"] == "codex") != (isinstance(turn_id, str) and bool(turn_id.strip())):
        return False
    identity = row.get("identity")
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS
            or not valid_local_identity(identity)):
        return False
    ids = row.get("rule_ids")
    if not isinstance(ids, list) or not ids or ids != sorted(set(ids)):
        return False
    rules, overflow, not_found = row.get("rules"), row.get("overflow"), row.get("not_found")
    if (not isinstance(rules, list) or not isinstance(overflow, list)
            or not isinstance(not_found, list)):
        return False
    if any(not isinstance(r, dict) or set(r) != RULE_KEYS
           or not isinstance(r.get("statement"), str) or not r["statement"].strip()
           for r in rules):
        return False
    if any(not isinstance(o, dict) or set(o) != {"id", "summary"} for o in overflow):
        return False
    parts = [r["id"] for r in rules] + [o["id"] for o in overflow] + list(not_found)
    if sorted(parts) != ids or len(parts) != len(set(parts)):
        return False
    if not set(row.get("route_rule_ids") or []) <= set(ids):
        return False
    delivery = row.get("rule_delivery")
    if (not isinstance(delivery, dict) or set(delivery) != DELIVERY_KEYS
            or delivery.get("mode") not in {"shadow", "enforced"}
            or sorted(delivery.get("declared_packs") or []) != row.get("packs")
            or delivery.get("packs_not_found") != []):
        return False
    if row.get("instruction") != ROUTE_INSTRUCTION:
        return False
    repo = Path(repo)
    if (row.get("routes_digest") != routes_digest(repo)
            or row.get("triggers_digest") != file_sha256(repo / TRIGGER_TABLE_RELATIVE)
            or row.get("map_digest") != file_sha256(
                repo / "ops/config/rule-enforcement-map.json")
            or row.get("source_digest") != source_sha256(repo)):
        return False
    return row.get("receipt_id") == receipt_id(row)
