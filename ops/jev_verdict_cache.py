"""jev_verdict_cache.py — reuse a Jev verdict for an input that is byte-identical.

WHY THIS EXISTS, measured on 2026-09-25 from out/jev-calls.jsonl and
out/jev-rule-select.jsonl. The rule selector (ops/jev_rule_select.py) asks one
ranking Choice plus one binding Noul per shortlisted rule every time a prompt
reaches UserPromptSubmit, and the Stop-side review triage
(ops/jev_done_checks.triage_review) re-scores the same unchanged working-tree
diff at every Stop. Both were paying again for a question whose every input —
the text judged, the rule set or diff, and the code that asks — was identical
to one answered minutes earlier.

THIS FILE IS ONLY THE STORE. What a key covers is each caller's decision and
is documented there: the review triage keys on the exact diff and task, the
build advisory on the exact request, and the rule selector on (session, rule,
pack, input class) — see CACHE_PATH in ops/jev_rule_select.py, including the
delivery trade that class key makes.

EVERY FAILURE IS A MISS, NEVER A SILENCE. An unreadable, corrupt, expired or
unwritable cache makes the caller ask Jev exactly as it did before this file
existed. Nothing here raises. Callers must only store a COMPLETE answer — a
partial or failed judgment is never cached, so an outage is never replayed as
if it were a negative.

A LIBRARY, NOT A SCRIPT: no shebang and no entrypoint construct, for the
sealed source inventory reason ops/typesafe_client.py documents.
"""

import hashlib
import json
import os
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Thirty minutes. Replay of 2026-09-25 gave 9% fewer selections at 10 minutes,
# 10% at 30 and 11% at 60: repeats cluster close together, so a longer window
# buys little and keeps a stale answer around longer after the world moves on
# in ways the key cannot see (a rule re-taught with identical text, say).
DEFAULT_TTL_SECONDS = 30 * 60

# Bounded so the file never grows without limit; oldest entries go first.
MAX_ENTRIES = 512


def source_digest(*relative_paths):
    """sha256 over the named repo files, so a code change invalidates the cache.

    A missing file contributes a fixed marker rather than raising: the key
    still changes when the file set changes, and a read failure here must not
    stop the caller from asking."""
    digest = hashlib.sha256()
    for relative in relative_paths:
        digest.update(relative.encode("utf-8") + b"\0")
        try:
            with open(os.path.join(REPO, relative), "rb") as handle:
                digest.update(handle.read())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def key(material):
    """A stable content key for any JSON-able material."""
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def get(path, cache_key, *, ttl=DEFAULT_TTL_SECONDS, now=None):
    """The stored value for `cache_key` if it is younger than `ttl`, else None."""
    try:
        now = time.time() if now is None else now
        entry = _load(path).get(cache_key)
        if not isinstance(entry, dict):
            return None
        stored_at = entry.get("at")
        if not isinstance(stored_at, (int, float)) or not 0 <= now - stored_at <= ttl:
            return None
        return entry.get("value")
    except Exception:
        return None


def put(path, cache_key, value, *, ttl=DEFAULT_TTL_SECONDS, now=None,
        max_entries=MAX_ENTRIES):
    """Store `value`. Returns True on success; any failure returns False."""
    return put_many(path, {cache_key: value}, ttl=ttl, now=now, max_entries=max_entries)


def put_many(path, values, *, ttl=DEFAULT_TTL_SECONDS, now=None,
             max_entries=MAX_ENTRIES):
    """Store every {key: value} in one rewrite. True on success, else False.

    Written through a temporary file and an atomic rename, so two hooks racing
    can lose one entry (a later extra ask) but can never leave a torn file."""
    tmp = None
    try:
        now = time.time() if now is None else now
        entries = {k: v for k, v in _load(path).items()
                   if isinstance(v, dict) and isinstance(v.get("at"), (int, float))
                   and 0 <= now - v["at"] <= ttl}
        for cache_key, value in values.items():
            entries[cache_key] = {"at": now, "value": value}
        if len(entries) > max_entries:
            keep = sorted(entries, key=lambda k: entries[k]["at"])[-max_entries:]
            entries = {k: entries[k] for k in keep}
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".jev-cache-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"entries": entries}, handle, sort_keys=True, default=str)
        os.replace(tmp, path)
        tmp = None
        return True
    except Exception:
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
