"""The verify re-read binding on V5-F08 recovery evidence (review G4, decided design).

A verify step PERFORMS EVERY DECISIVE READ ITSELF (review H1: nothing that
decides a verdict may come from a caller file) and then stamps the result with

    verification = {"verifier": <registered id>, "verified_at": <UTC instant>,
                    "facts_digest": "sha256:" + sha256(canonical facts)}

where the facts are the evidence without its `verification` block, serialised
exactly as mcp-server/src/artifact-trust.js canonicalJson does. The evaluator
(mcp-server/src/recovery-matrix.v5.js) accepts a piece of evidence only when
the verifier is the one registered for its kind, the digest recomputes, and
verified_at is no more than 15 minutes before the evaluator's own clock — so
evidence must be re-verified at the time it is judged, and a file edited after
verification fails.

WHAT IT IS NOT: a signature. There is no signing key, so someone who
deliberately recomputes the digest can forge one. The binding stops stale
evidence, evidence edited after its re-read, and evidence that skipped the
re-read path; authenticating the verifier would need a key the repository does
not hold.

WHAT IS AUTHORITY (review K3): only the output of `restore-watermark.py
verify-restore` or `pitr-restore-proof.py prove` PIPED straight into
mcp-server/bin/recovery-matrix-evaluate.mjs. A bound file saved to disk is the
operator's copy; an evaluator verdict on that file is not a recovery result,
because nothing proves the file is what the verifier printed.

CLOCK ASSUMPTION (review K4): verified_at and the receipt instants come from
the verifying machine's clock. Each verifier cross-checks that clock against
the database server it just read and refuses a skew over MAX_CLOCK_SKEW_SECONDS.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# The tables a real restore of the record layer must carry rows in: the restore
# verifier requires each in the artifact watermark with rows > 0 (review K2),
# and the PITR proof counts them on its branch.
CORE_TABLES = ("public.party", "ops.run")

MAX_CLOCK_SKEW_SECONDS = 180


def check_clock_skew(local: datetime, server: datetime, what: str) -> None:
    """Refuse when this machine's clock and the server's differ by more than MAX_CLOCK_SKEW_SECONDS."""
    skew = abs((local - server).total_seconds())
    if skew > MAX_CLOCK_SKEW_SECONDS:
        raise ValueError(f"the local clock differs from {what}'s by {skew:.0f}s (limit {MAX_CLOCK_SKEW_SECONDS}s); "
                         "fix the clock before verifying")


VERIFIERS = {
    "restore_exercise": "tools/restore-watermark.py verify-restore",
    "record_layer_rpo": "tools/pitr-restore-proof.py prove",
    "outbound_census": "tools/restore-watermark.py outbound-census",
}


def canonical_json(value: Any) -> str:
    """Byte-for-byte the same text as artifact-trust.js canonicalJson for JSON data."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def facts_digest(facts: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(facts).encode("utf-8")).hexdigest()


def bind(facts: dict[str, Any], kind: str, now: datetime | None = None) -> dict[str, Any]:
    """Return the facts with a fresh verification block. `facts` must not carry one already."""
    if "verification" in facts:
        raise ValueError("evidence already carries a verification block; re-derive it instead of re-stamping")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {**facts, "verification": {"verifier": VERIFIERS[kind], "verified_at": stamp,
                                      "facts_digest": facts_digest(facts)}}
