"""Best-effort redaction of secrets from untrusted child-process output.

Deterministic control-plane entrypoints run without ledger, provider, owner,
or live-ingest credentials in their own environment (see
``tools/control-plane.py``'s ``_execute_deterministic``). That boundary keeps
a child from being HANDED a secret; it does not stop the child's stdout or
stderr from ECHOING one anyway -- a misconfigured downstream call, a stack
trace that prints a DSN, a copy-pasted example. Failure evidence is the one
place raw child output is about to be written into a stored receipt, so it is
the one place this module is required to run first.

This is deliberately best-effort, not a guarantee: secret shapes evolve and a
genuinely novel one can still slip through. It is layered defense on top of
the credential-exclusion boundary above, not a replacement for it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

MASK = "[REDACTED]"

# scheme://user:password@host... -- mask the WHOLE authority-bearing URI, not
# just the password, since host/database name can be sensitive too.
_URI_WITH_CREDENTIALS = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"/@]+:[^\s'\"@]*@[^\s'\"]+")

# Recognisable API-key / access-token prefixes (OpenAI-shaped, GitHub, AWS,
# Slack...).
_PREFIXED_TOKEN = re.compile(
    r"\bsk-[A-Za-z0-9_\-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9_\-]{16,}"
    r"|\bgithub_pat_[A-Za-z0-9_\-]{16,}"
    r"|\bxox[baprs]-[A-Za-z0-9_\-]{10,}"
    r"|\bAKIA[A-Z0-9]{12,}")

# A long hex/base64-shaped run immediately after ':' or '=' -- e.g.
# "token: abcd1234...", "PASSWORD=deadbeef...". Explicit diagnostic
# identifier fields are exempted below only when the value has the right shape.
_ASSIGNED_SECRET = re.compile(
    r"(?<=[:=])\s*[A-Za-z0-9+/_\-]{32,}={0,2}(?=[\s'\",;]|$)")

# Field boundaries keep "token source_sha=..." from masquerading as a
# public identifier. Shape alone is insufficient: a password may be a SHA or UUID.
_PUBLIC_IDENTIFIER_LABEL = re.compile(
    r"(?:^|[\n;,])\s*(?:(?P<sha>(?:source[ _-])?sha(?: mismatch)?)"
    r"|(?P<receipt>receipt[ _-]id))\s*[:=]$", re.IGNORECASE)
_SHA_IDENTIFIER = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.IGNORECASE)
_RECEIPT_IDENTIFIER = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE)


def _redact_assigned_value(match: re.Match[str]) -> str:
    label = _PUBLIC_IDENTIFIER_LABEL.search(match.string[:match.start()])
    if label:
        shape = _SHA_IDENTIFIER if label.group("sha") else _RECEIPT_IDENTIFIER
        if shape.fullmatch(match.group().strip()):
            return match.group()
    return MASK


# Env-var NAMES the runner treats as credential-shaped -- the same words that
# already keep a deterministic child's environment free of ledger, provider,
# owner and live-ingest secrets (tools/control-plane.py, ``_execute_deterministic``).
SENSITIVE_ENV_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|URL", re.IGNORECASE)

# Below this length a "secret" value is common noise ("1", "true", "") and
# masking it would make ordinary diagnostic text unreadable for no safety gain.
_MIN_KNOWN_SECRET_LENGTH = 6


def sensitive_env_values(env: Mapping[str, str]) -> list[str]:
    """Values from ``env`` whose NAME looks credential-shaped.

    Longest first: a short value that is itself a substring of a longer one
    (e.g. a bare token embedded inside a full connection-string value) must
    never mask ahead of -- and thereby leave half exposed -- the longer one.
    """
    values = {value for name, value in env.items()
              if value and len(value) >= _MIN_KNOWN_SECRET_LENGTH
              and SENSITIVE_ENV_NAME.search(name)}
    return sorted(values, key=len, reverse=True)


def redact_text(text: str, *, known_secrets: Iterable[str] = ()) -> str:
    """Mask credential-shaped substrings in ``text``.

    Order matters relative to bounding: this must run on the FULL text before
    any truncation to a tail. Redacting-then-truncating is the only order
    that can never hand back a truncated half of a secret whose prefix (and
    therefore whose matching pattern) fell outside the kept window.
    """
    if not text:
        return text
    out = text
    for secret in known_secrets:
        if secret:
            out = out.replace(secret, MASK)
    out = _URI_WITH_CREDENTIALS.sub(MASK, out)
    out = _PREFIXED_TOKEN.sub(MASK, out)
    # Known secrets and credential shapes take precedence over public labels.
    out = _ASSIGNED_SECRET.sub(_redact_assigned_value, out)
    return out


def redacted_tail(text: str | None, *, known_secrets: Iterable[str] = (),
                   limit: int = 2000) -> str:
    """Redact ``text``, then bound it to its trailing ``limit`` characters."""
    return redact_text(text or "", known_secrets=known_secrets)[-limit:]
