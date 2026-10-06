"""Bounded Grok CLI retrieval through the existing authenticated subscription.

The answer's prose cannot certify a model. Only the CLI's terminal modelUsage
event does; absent, mixed or mismatched provider metadata refuses completion.
No token is loaded here, and no other provider is a fallback.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import parse_qsl, urlsplit, urlunsplit

MODEL = "grok-4.7"
PROVIDER_MODEL = "grok-4.7-build"
EFFORT = "high"
TIMEOUT_S = 180.0
MAX_TURNS = 60


def requested_urls(task: str) -> list[str]:
    urls = []
    for url in re.findall(r"https?://[^\s<>\"'`*]+", task, re.IGNORECASE):
        url = url.rstrip(".,;!?")
        while url and url[-1] in ")]}":
            opening = {")": "(", "]": "[", "}": "{"}[url[-1]]
            if url.count(url[-1]) <= url.count(opening):
                break
            url = url[:-1].rstrip(".,;!?")
        if url not in urls:
            urls.append(url)
    return urls


def public_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        if (url.scheme not in {"https", "http"} or not host or url.username or url.password
                or any(c.isspace() or ord(c) < 32 for c in value) or "%" in host
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
            return False
        # Validate malformed ports even when no caller uses the port.
        _ = url.port
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            if "." not in host or re.fullmatch(r"[0-9.]+", host):
                return False
        for key, _ in parse_qsl(url.query, keep_blank_values=True):
            key = re.sub(r"[^a-z0-9]", "", key.lower())
            if (key in {"key", "sig", "auth", "authorization", "password", "passwd"}
                    or any(part in key for part in ("token", "signature", "credential", "secret", "apikey"))):
                return False
        return True
    except ValueError:
        return False


def normalized_url(value: str) -> str:
    url = urlsplit(value)
    host = (url.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    if url.port and (url.scheme, url.port) not in {("https", 443), ("http", 80)}:
        host += f":{url.port}"
    return urlunsplit((url.scheme.lower(), host, url.path.rstrip("/") or "/", url.query, url.fragment))


def retrieval_request_error(urls: list[str]) -> dict | None:
    if not urls or any(not public_url(url) for url in urls):
        return {"status": "failed", "code": 6, "detail": "invalid_retrieval_url",
                "next_route": "Use a public source URL without embedded credentials."}
    return None


def retrieval_prompt(urls: list[str]) -> str:
    if not urls:
        return ""
    shape = {"retrieval": {"requested_urls": urls, "sources": [{"url": urls[0],
        "text": "verbatim retrieved source text"}],
        "source_urls": urls, "unresolved_portions": [], "status": "complete or partial"}}
    return ("Return a JSON object in this shape: " + json.dumps(shape) +
        ". Each source requires its public URL and verbatim retrieved text in the text field. "
        "Local artifact paths are not source evidence. Return only this object, optionally in one JSON fence. "
        "List missing thread, quoted post, linked source or media portions explicitly. "
        "Never substitute an acknowledgment or summary for the source. "
        "If the caller requires a trailing CARR_QUEUE_RESULT line, put it after the JSON object.\n\n")


def retrieval_result(text: str, urls: list[str]) -> dict:
    missing = {"requested_urls": urls, "sources": [], "source_urls": [],
               "unresolved_portions": urls, "status": "unusable_retrieval"}
    refusal = {"status": "failed", "code": 6, "detail": "unusable_retrieval", "retrieval": missing,
        "next_route": "Read the requested source through its canonical repository or browser; verify the raw source before use."}
    source_text, marker, terminal = text.rpartition("\nCARR_QUEUE_RESULT ")
    source_text = (source_text if marker else text).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", source_text, re.DOTALL | re.IGNORECASE)
    if fenced:
        source_text = fenced[1]
    try:
        payload = json.loads(source_text)
    except ValueError:
        return refusal
    record = payload.get("retrieval") if isinstance(payload, dict) else None
    if not isinstance(record, dict):
        return refusal
    echoed = record.get("requested_urls")
    if (not isinstance(echoed, list) or not all(public_url(url) for url in echoed)
            or [normalized_url(url) for url in echoed] != [normalized_url(url) for url in urls]):
        return refusal
    sources, source_urls, unresolved = (record.get(key) for key in
                                       ("sources", "source_urls", "unresolved_portions"))
    if (record.get("status") not in ("complete", "partial") or not isinstance(sources, list)
            or not isinstance(source_urls, list) or not all(public_url(url) for url in source_urls)
            or not isinstance(unresolved, list) or not all(isinstance(p, str) and p.strip() for p in unresolved)):
        return refusal
    evidence = []
    for source in sources:
        if not isinstance(source, dict) or source.get("url") not in source_urls:
            return refusal
        if not isinstance(source.get("text"), str) or not source["text"].strip():
            return refusal
        item = {"url": source["url"], "text": source["text"]}
        evidence.append(item)
    requested = {normalized_url(url) for url in urls}
    retrieved = {normalized_url(source["url"]) for source in evidence}
    if not requested & retrieved:
        return refusal
    unresolved = list(dict.fromkeys([*unresolved, *(url for url in urls
        if normalized_url(url) not in retrieved)]))
    retrieval = {"requested_urls": urls, "sources": evidence, "source_urls": source_urls,
        "unresolved_portions": unresolved,
        "status": "partial" if unresolved or record["status"] == "partial" else "complete"}
    result = json.dumps({"retrieval": retrieval})
    if marker:
        # The queue's existing task/capability validator owns this protocol.
        result += marker + terminal
    return {"status": "completed", "code": 0, "result": result, "retrieval": retrieval}


def write_receipt(receipt: dict, path=None) -> str:
    """Keep only adapter-derived diagnostics; never provider stderr, prompts or source text."""
    if path:
        path = Path(path).expanduser().absolute()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    else:
        root = Path.home() / ".local/state/carr/grok-runs"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix="retrieval-", suffix=".json", dir=root)
        path = Path(name)
    receipt["diagnostic_path"] = str(path)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(receipt, stream, sort_keys=True)
        stream.write("\n")
    return str(path)

def validate_entry(entry: dict) -> None:
    if (entry.get("model") != MODEL or entry.get("effort") != EFFORT
            or entry.get("sandbox") != "read-only"):
        raise ValueError("Grok retrieval requires grok-4.7/high/read-only")


def model_usage_error(models) -> str | None:
    """Validate the complete provider model set and evidence of a model call."""
    if not isinstance(models, dict) or set(models) != {PROVIDER_MODEL}:
        return "grok_provider_model_mismatch"
    usage = models[PROVIDER_MODEL]
    if not isinstance(usage, dict) or type(usage.get("modelCalls")) is not int or usage["modelCalls"] < 1:
        return "grok_provider_usage_invalid"
    return None


def parse_stream(lines, returncode: int = 0) -> dict:
    """Keep the latest assistant response with exactly one end.

    These events do not identify which task produced each response. Prose
    cannot establish lifecycle provenance or authorize reusing an older answer.
    The verified read-only invocation suppresses lifecycle hooks at the source.
    """
    chunks = []
    final_chunks = []
    end: dict = {}
    detail = None
    for line in lines:
        if not line.strip():
            continue
        if end:
            detail = "grok_terminal_event_missing_or_failed"
            break
        try:
            event = json.loads(line)
        except ValueError:
            detail = "grok_invalid_stream"
            break
        if not isinstance(event, dict):
            detail = "grok_invalid_stream"
            break
        if event.get("type") == "error":
            detail = "grok_error_event"
            break
        if event.get("type") == "text":
            if not isinstance(event.get("data"), str):
                detail = "grok_invalid_stream"
                break
            chunks.append(event["data"])
        elif event.get("type") == "usage":
            # A response boundary saves its chunks, including an explicitly
            # identified empty response. Older streams omit messageId, so text
            # also establishes a boundary. Accounting alone cannot erase it.
            if chunks or event.get("messageId"):
                final_chunks = chunks
                chunks = []
        elif event.get("type") == "end":
            end = event
    if chunks:
        final_chunks = chunks
    text = "".join(final_chunks)
    code = 0
    if detail:
        end = {**end, "stopReason": "invalid_stream"}
        code = 4
    elif returncode != 0 or not end:
        detail, code = "grok_terminal_event_missing_or_failed", 4
    elif end.get("stopReason") != "end_turn":
        detail, code = "grok_incomplete_turn", 4
    else:
        detail = model_usage_error(end.get("modelUsage"))
        if detail:
            code = 5
    return {"text": text, "end": end, "detail": detail, "code": code}


def provider_receipt(end: dict, cli_version=None) -> dict:
    models = end.get("modelUsage", {})
    cost = end.get("total_cost_usd", end.get("cost_usd"))
    turns = end.get("num_turns")
    return {"requested_model": MODEL, "actual_models": sorted(models) if isinstance(models, dict) else [],
               "stopReason": end.get("stopReason") if isinstance(end.get("stopReason"), str) else None,
               "num_turns": turns if type(turns) is int else None,
               "cost_usd": cost if type(cost) in (int, float) else None, "cli_version": cli_version}


def parse_result(stdout: str, returncode: int, *, urls: list[str] | None = None,
                 effort=EFFORT, cli_version=None, require_identity=True) -> dict:
    parsed = parse_stream(stdout.splitlines(), returncode)
    end = parsed["end"]
    receipt = provider_receipt(end, cli_version)
    if parsed["code"]:
        return {"status": "failed", "detail": parsed["detail"], "code": parsed["code"],
                "result": "" if urls else parsed["text"], "receipt": receipt}
    if require_identity and not all(isinstance(end.get(key), str) and end[key].strip()
                                    for key in ("requestId", "sessionId")):
        return {"status": "failed", "code": 4, "detail": "grok_provider_identity_missing", "receipt": receipt}
    result = parsed["text"].strip()
    if not result and not urls and require_identity:
        return {"status": "failed", "code": 4, "detail": "grok_empty_result", "receipt": receipt}
    # Select metadata explicitly: tool arguments, diagnostics, credentials and
    # arbitrary future envelope fields never enter the dispatch receipt.
    usage = end["modelUsage"][PROVIDER_MODEL]
    metadata = {"requested_model": MODEL, "actual_model": PROVIDER_MODEL,
                "effort": effort, "request_id": end.get("requestId"),
                "session_id": end.get("sessionId"), "model_calls": usage["modelCalls"],
                "cost_usd": receipt["cost_usd"], "stop_reason": end["stopReason"]}
    outcome = retrieval_result(result, urls) if urls else {
        "status": "completed", "code": 0, "result": result if require_identity else parsed["text"]}
    return {**outcome, "provider_metadata": metadata, "receipt": receipt}


def invoke_cli(prompt: str, *, cwd=None, effort=EFFORT, max_turns=MAX_TURNS,
               writable=False, timeout_seconds=TIMEOUT_S, run=subprocess.run):
    """The sole model-work invocation; the desk retains its 180-second default."""
    argv = ["grok", "--model", MODEL, "--reasoning-effort", effort,
            "--max-turns", str(max_turns), "--always-approve",
            "--sandbox", "workspace" if writable else "read-only",
            "--output-format", "streaming-json", "--print", prompt]
    env = dict(os.environ)
    env.pop("CARR_GROK_RUN_READ_ONLY", None)
    if not writable:
        env["CARR_GROK_RUN_READ_ONLY"] = "1"
    return run(argv, cwd=cwd, capture_output=True, text=True,
               stdin=subprocess.DEVNULL, timeout=timeout_seconds, env=env)


class PreflightError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def run_request(task: str, *, retrieval=False, prefix="", cwd=None, effort=EFFORT,
                max_turns=MAX_TURNS, writable=False, timeout_seconds=TIMEOUT_S,
                invoke=invoke_cli, preflight=None, fixture=None, require_identity=True,
                receipt_path=None) -> dict:
    """One execution owns URL validation, provider outcome codes and private receipts.

    Every prompt URL must be public and free of embedded credentials before
    invocation. Retrieval output requires explicit opt-in and source text;
    local files cannot establish provenance.
    """
    prompt_urls = requested_urls(task)
    outcome = retrieval_request_error(prompt_urls) if prompt_urls or retrieval else None
    urls = prompt_urls if retrieval else []
    cli_version = None
    if outcome is None:
        try:
            prompt = prefix + "\n\n" + retrieval_prompt(urls) + task
            if fixture:
                raw, returncode, cli_version = Path(fixture).read_text(encoding="utf-8"), 0, "fixture"
            else:
                if preflight:
                    cli_version = preflight()
                proc = invoke(prompt, cwd=cwd, effort=effort, max_turns=max_turns,
                              writable=writable, timeout_seconds=timeout_seconds)
                raw, returncode = proc.stdout or "", proc.returncode
            outcome = parse_result(raw, returncode, urls=urls, effort=effort, cli_version=cli_version,
                                   require_identity=require_identity or retrieval)
        except PreflightError as error:
            outcome = {"status": "failed", "code": error.code,
                       "detail": "grok_sign_in_required" if error.code == 3 else "grok_preflight_failed"}
        except FileNotFoundError:
            outcome = {"status": "failed", "code": 4, "detail": "grok_cli_unavailable"}
        except subprocess.TimeoutExpired:
            outcome = {"status": "timed_out", "code": 4,
                       "detail": "grok_preflight_timeout" if cli_version is None and preflight else "grok_cli_timeout"}
        except (OSError, ValueError):
            outcome = {"status": "failed", "code": 4, "detail": "grok_invocation_failed"}
    metadata = outcome.get("provider_metadata", {})
    receipt = {**outcome.pop("receipt", provider_receipt({}, cli_version)),
               "effort": effort, "status": outcome["status"],
               "code": outcome["code"], "detail": outcome.get("detail"),
               "task_sha256": hashlib.sha256(task.encode()).hexdigest(), "max_turns": max_turns,
               "sandbox": "workspace" if writable else "read-only", "timeout_seconds": timeout_seconds,
               "retrieval_status": outcome.get("retrieval", {}).get("status"),
               "actual_model": metadata.get("actual_model"), "model_calls": metadata.get("model_calls"),
               "diagnostic_path": None}
    if receipt_path or retrieval:
        try:
            outcome["diagnostic_path"] = write_receipt(receipt, path=receipt_path)
        except OSError:
            outcome = {"status": "failed", "code": 4, "detail": "grok_receipt_unavailable"}
            receipt.update(status="failed", code=4, detail=outcome["detail"], diagnostic_path=None)
    return {**outcome, "receipt": receipt}


def run_task(entry: dict, task: str, *, retrieval=False, run=subprocess.run) -> dict:
    validate_entry(entry)
    return run_request(task, retrieval=retrieval, cwd=entry.get("cwd"),
        prefix=("Read-only retrieval or explanation only. Do not call CARR or MCP tools, "
                "read credential/config files, write files, or delegate. Return a bounded "
                "answer with public source URLs when retrieving."),
        invoke=lambda prompt, **kw: invoke_cli(prompt, **kw, run=run))
