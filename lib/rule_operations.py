"""Pending-operation facts shared by rule selection consumers."""
from __future__ import annotations

import re
import unicodedata

PATH_INPUT_KEYS = ("file_path", "path", "notebook_path")
READ_ONLY_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})
BASH_TOOLS = frozenset({"Bash", "functions.exec"})
RUN_SH_CALL = re.compile(r"\brun\.sh\s+call\s+['\"]?([a-z0-9][a-z0-9-]*)", re.I)
CALL_VERB_PY = re.compile(r"\bcall-verb\.py\s+['\"]?([a-z0-9][a-z0-9-]*)", re.I)


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
