"""jev_judge.py — ask Jev a narrow question about a body of text, and RECORD it.

WHAT THIS IS FOR. CARR decides a great many things by matching strings: whether
a commit fixes the problem a claim describes, whether a response accounts for a
clause, whether a defect belongs in a class that already exists, whether a
doctrine section answers a question. Every one of those is a judgment about
text wearing a keyword table's clothing, and every one of them has misfired.
This module is the one place those judgments get asked properly.

IT IS A LIBRARY AND MUST STAY ONE. No shebang, no main guard, for the reason
ops/typesafe_client.py spells out at length: either one makes a .py file a
registered script entrypoint in the sealed source inventory, moves the frontier
and owes a forward-only registry successor. Callers are entrypoints that exist.
The detector is a regex over the whole file and does not know what a docstring
is, so describe the construct and never spell it.

record() IS THE AUDIT LOG, NOT A WAITING ROOM (Joe, 2026-09-24, decision
5ec806a4: "every jev check in the system too is not a shadow"). It used to be
that a caller wrote what the judgment WOULD have decided next to what the
existing mechanism DID decide, and returned without acting, moving to acting
only once the log showed agreement on real traffic. That default is retired:
every Jev check now acts on its own judgment. record() still writes what Jev
said beside what the caller decided, on every call, acting or not — the log is
now how each acting use is audited, not a precondition for turning it on. What
does not change: each acting use keeps its threshold (the value in place when
it started shadowing is the STARTING point, not re-guessed at the switchover),
keeps an abstention path (JudgeUnavailable, any other failure, or a low
confidence falls back to the caller's prior non-Jev behavior, visibly, never
an affirmative judgment), and keeps recording every judgment through this
function. This matters because a typed answer guarantees an answer's shape and
never its truth: a caller that acts on an unvalidated threshold converts a
model's uncertainty into a partner's blocked afternoon, so the threshold
still has to come from measurement, just not from a phase gate on this module.

THREE THINGS LEARNED THE EXPENSIVE WAY ON 2026-09-18, all of them shaping the
interface below:

  1. PICK THE SHAPE FROM THE JOB, AND THERE ARE TWO SHAPES. This entry said
     "one request per subject, never one request carrying every subject" as
     though it were a law. That is the RERANKING rule, it is real, and it is
     narrow: the vendor's reranking walkthrough scores query-and-candidate
     pairs one request at a time, no request seeing another — over a SHORTLIST
     OF THIRTY that a keyword search produced first. Reading it as universal
     was wrong and it cost this repository four modules built the expensive way.

     SELECTING FROM A ROSTER IS THE OTHER SHAPE, and the vendor's own examples
     are exactly that: 182 agent skills ranked in ONE Choice question, 218
     document line identifiers scored in ONE request. A Choice carries up to
     255 options, its probabilities sum to one across them, and an explicit
     "none of these fits" option is how it declines. Then, if anything, a close
     look at the top two or three.

     MEASURED HERE ON 2026-09-18, same held-out data, same corpus, the two
     shapes against each other on picking a defect class from 320:

         one Noul per class ....... 320 requests  12.5s  38% top-1  81% top-8
         one Choice, then a look ...  9 requests   1.4s  69% top-1  88% top-8

     And on finding the commit that answers a claim, over 214 commits: 214
     requests and 4.2 seconds became ONE request and 0.6 seconds, with the same
     four answers out of four, and with "nothing here answers this" arriving as
     a calibrated option rather than as a hand-set floor.

     THE TEST, so this does not get misread again: are the candidates competing
     for one slot, or is each independently true or false? Competing for a slot
     is a Choice over all of them. Independently true or false — several may
     apply, or none — is a Noul each, and then it belongs on a shortlist rather
     than on the whole roster. Truncate option text in the ranking pass and
     keep the full text for the close look; 255 full-length options returns
     HTTP 400 max_tokens_exceeded, which is how that stops being optional.

  1b. ASK EVERY INDEPENDENT QUESTION ABOUT ONE SUBJECT IN ONE REQUEST. This is
     the vendor's headline efficiency principle and the opposite of what the
     old entry above implied. Their measurement: thirteen questions about one
     document batched into a single call came out 12.2 times cheaper and 10
     times faster, and each question is scored on its own against the state, so
     an answer does not depend on what else rides along. A broad question that
     hides several judgments is the thing to avoid — decompose it into narrow
     ones and combine them in code, in ONE call, not in several.

  2. LOW CONFIDENCE IS THE ANSWER, NOT A FAILURE TO ANSWER. On the day this was
     written, Jev's wrong calls arrived under 0.5 confidence and its right ones
     above 0.8, twice in a row, on real code. A caller that ignores confidence
     throws away the most useful half of the signal.

  3. A QUESTION NOBODY THINKS TO ASK RETURNS NOTHING. Jev sharpened a review; it
     did not replace one. These judgments narrow where a human or a larger model
     looks. They do not decide that nobody needs to look.
"""

import json
import math
import os
import sys
import time
import uuid
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every judgment lands here, acted on or not. One JSON object per line,
# append-only, so a threshold can be measured from real traffic rather than
# argued for, and so an acting caller's decisions stay auditable afterward.
SHADOW_LOG = os.path.join(REPO, "out", "jev-judge.jsonl")

# Per-family, per-consequence-class entropy bands that route() acts inside.
# A band enters this file only from a held-out calibration report
# (ops/jev-calibration-report.py), in a reviewed commit; it is never pooled
# across families or classes and never borrowed from outside CARR.
BANDS_PATH = os.path.join(REPO, "ops", "config", "jev-calibrated-bands.v1.json")
BANDS_SCHEMA = "carr.jev-calibrated-bands.v1"

# Deliberately pessimistic defaults. THEY ARE PLACEHOLDERS: every caller is
# expected to replace them with numbers measured on its own shadow log, because
# the cost of a wrong call is wildly different between a commit-message warning
# and anything a partner or a client sees.
YES_AT = 0.80
NO_AT = 0.20
MIN_CONFIDENCE = 0.60


class JudgeUnavailable(RuntimeError):
    """The judgment could not be obtained. NEVER a reason to fail a caller.

    A gate that hard-fails when an outside service is slow is a gate that turns
    someone else's outage into this repository's outage. Callers catch this and
    proceed on their existing mechanism; record() notes the miss so an outage
    is visible in the log rather than silently reducing coverage.
    """

    def __init__(self, message, *, reason="inspection_error"):
        super().__init__(message)
        self.reason = reason


def _client():
    """Import the vendor client lazily, so importing this module costs nothing."""
    import importlib.util
    path = os.path.join(REPO, "ops", "typesafe_client.py")
    spec = importlib.util.spec_from_file_location("typesafe_client", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise JudgeUnavailable("cannot load ops/typesafe_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _calling_module():
    """The file name (no extension) of the code that called judge()."""
    try:
        return os.path.splitext(os.path.basename(sys._getframe(2).f_code.co_filename))[0]
    except (AttributeError, ValueError):
        return "unknown"


def judge(subject, questions, *, timeout=20.0, client=None, api_key=None,
          retries=None, deadline=None, model=None, caller=None):
    """Ask every question in `questions` about ONE subject, in one request.

    `subject` is a mapping describing the single thing being judged — a diff, a
    response, a defect, a candidate section. `questions` maps a caller-chosen id
    to a question built with the client's noul/choice/score helpers.

    Returns {"answers": {...}, "usage": {...}, "model": ..., "elapsed_ms": int}.
    Raises JudgeUnavailable for ANY failure, including a missing credential, so
    a caller has exactly one thing to catch.

    The timeout is short on purpose. This is meant to be called from hooks and
    gates that sit in someone's way, and a judgment that has not arrived in
    twenty seconds has already cost more than it is worth.

    `retries` and `deadline` pass through to the client's ask() when given
    (typesafe_client.ask: rate-limit retries, and an absolute monotonic
    deadline). A caller under a hook timeout passes retries=0: a 429's
    retry-after is otherwise unbounded.
    """
    started = time.monotonic()
    tsc = None
    try:
        tsc = client or _client()
        extra = {}
        if retries is not None:
            extra["retries"] = retries
        if deadline is not None:
            extra["deadline"] = deadline
        if model is not None:
            extra["model"] = model
        if hasattr(tsc, "JUDGE_CACHE_TTL_SECONDS"):
            # The call site is the module that asked, not this wrapper: every
            # judge() caller used to log as "jev_judge", which hid 95% of paid
            # calls behind one name. ops/config/jev-call-sites.v1.json keys on it.
            extra.update(caller=caller or _calling_module(),
                         cache_ttl_seconds=tsc.JUDGE_CACHE_TTL_SECONDS)
        answer = tsc.ask(subject, questions, timeout=timeout, api_key=api_key, **extra)
    except Exception as exc:  # deliberately broad: see JudgeUnavailable
        reason = (getattr(exc, "code", None) or "vendor_unavailable"
                  if isinstance(exc, getattr(tsc, "TypeSafeError", ())) else "inspection_error")
        raise JudgeUnavailable(f"{type(exc).__name__}: {exc}", reason=reason) from None
    answer["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return answer


def read(answer, key, *, yes_at=YES_AT, no_at=NO_AT, min_confidence=MIN_CONFIDENCE):
    """One answer, turned into an act: {"outcome", "escalate", "value", "confidence"}.

    Thin wrapper over the client's own decide(), kept here so a caller imports
    one module rather than two, and so the escalate contract stays identical
    everywhere: escalate true means a person or a larger model looks, and the
    caller does NOT act on the value by itself.
    """
    tsc = _client()
    return tsc.decide(answer["answers"][key], yes_at=yes_at, no_at=no_at,
                      min_confidence=min_confidence)


def record(kind, subject_ref, answer, existing_decision=None, *, note=None,
           log_path=SHADOW_LOG, error=None, family=None, consequence_class=None,
           downstream_action=None, receipt_id=None):
    """Append one observation. Writes what Jev said BESIDE what we did.

    `existing_decision` is what the mechanism in place actually decided, so the
    log answers the only question that matters before switching anything on:
    on real traffic, how often do they disagree, and who was right? Pass it even
    when it is None — a row with no comparison is still evidence about coverage.

    CALIBRATION FIELDS. Each row gets a `judgment_id`, the handle an outcome
    (a review verdict, a test result, a human correction) is later joined to
    through ops/jev_calibration.py. `family` names the question family and
    `consequence_class` what a wrong call costs; `downstream_action` is what the
    caller then did (acted, routed to review, fell back). Each may be one value
    for every question or a {question id: value} mapping. `calibration` keeps
    the full distribution and entropy per question: the client's block when the
    answer came through ask(), else one rebuilt from the answers alone.
    `receipt_id` links a Worker ask-jev receipt when the call went that way.

    Never raises. A logging failure must not take down the caller it was added
    to observe; that would be the tail wagging the dog.
    """
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "judgment_id": str(uuid.uuid4()),
        "kind": kind,
        "subject_ref": subject_ref,
        "existing_decision": existing_decision,
        "note": note,
        "family": family,
        "consequence_class": consequence_class,
        "downstream_action": downstream_action,
        "receipt_id": receipt_id,
    }
    if error is not None:
        row["error"] = str(error)
    else:
        row["model"] = answer.get("model")
        row["usage"] = answer.get("usage")
        row["cache_hit"] = answer.get("cache_hit", False)
        row["elapsed_ms"] = answer.get("elapsed_ms")
        row["answers"] = answer.get("answers")
        row["calibration"] = _calibration_of(answer)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass
    return row


def _calibration_of(answer):
    """The client's calibration block, or one rebuilt from the answers alone."""
    block = answer.get("calibration")
    if isinstance(block, dict):
        return block
    try:
        tsc = _client()
        answers = answer.get("answers") if isinstance(answer.get("answers"), dict) else {}
        return {
            "schema": "carr.jev-calibration.v1",
            "model_requested": None,
            "model_answered": answer.get("model"),
            "model_pinned": None,
            "state_sha256": None,
            "questions": {key: tsc.answer_distribution(None, value)
                          for key, value in sorted(answers.items())},
        }
    except Exception:  # never let the log take the caller down
        return None


def load_bands(path=BANDS_PATH):
    """The committed calibrated bands. A missing or unreadable file calibrates nothing."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            bands = json.load(handle)
    except (OSError, ValueError):
        return {"schema": BANDS_SCHEMA, "bands": {}}
    return bands if isinstance(bands, dict) else {"schema": BANDS_SCHEMA, "bands": {}}


def _band_is_valid(band):
    limit = band.get("max_entropy_bits") if isinstance(band, dict) else None
    model = band.get("model") if isinstance(band, dict) else None
    return (not isinstance(limit, bool) and isinstance(limit, (int, float))
            and math.isfinite(limit) and limit >= 0
            and isinstance(model, str) and _client().model_is_pinned(model))


def _distribution_matches(recorded, observed):
    """The question may add offered zero-probability choices absent from the answer."""
    if not isinstance(recorded, dict) or not isinstance(observed, dict):
        return False
    return (all(recorded.get(key) == value for key, value in observed.items())
            and all(value == 0 for key, value in recorded.items() if key not in observed))


def route(answer, key, *, family, consequence_class, bands=None, bands_path=BANDS_PATH):
    """Act, or send to review: the decision is code's, from a measured band.

    Returns {"route": "act"|"review", "reason", "entropy_bits",
    "max_entropy_bits", "family", "consequence_class", "model"}. It acts only
    when this family AND this consequence class have a calibrated band, the
    band was measured on the model that answered, the answer has a full
    distribution, and its entropy is inside the band. Everything else is
    review, including every family nobody has calibrated yet. There is no
    pooled fallback and no default cutoff: an uncalibrated judgment is not
    acted on by itself.
    """
    bands = load_bands(bands_path) if bands is None else bands
    model = answer.get("model") if isinstance(answer, dict) else None
    tsc = _client()
    raw = answer.get("answers", {}).get(key) if isinstance(answer, dict) and isinstance(answer.get("answers"), dict) else None
    question = tsc.answer_distribution(None, raw) if isinstance(raw, dict) else None
    entropy = question.get("entropy_bits") if isinstance(question, dict) else None
    verdict = {"route": "review", "reason": None, "entropy_bits": entropy,
               "max_entropy_bits": None, "family": family,
               "consequence_class": consequence_class, "model": model}
    table = bands.get("bands") if isinstance(bands, dict) and bands.get("schema") == BANDS_SCHEMA else None
    if not isinstance(table, dict):
        return {**verdict, "reason": "band_invalid"}
    family_bands = table.get(family) if isinstance(table.get(family), dict) else None
    band = family_bands.get(consequence_class) if family_bands else None
    if "*" in (family, consequence_class) or "*" in table or (family_bands and "*" in family_bands):
        return {**verdict, "reason": "band_invalid"}
    if band is None:
        return {**verdict, "reason": "uncalibrated"}
    if not _band_is_valid(band):
        return {**verdict, "reason": "band_invalid"}
    verdict["max_entropy_bits"] = band["max_entropy_bits"]
    if model != band["model"]:
        return {**verdict, "reason": "model_mismatch"}
    if not tsc.model_is_pinned(model):
        return {**verdict, "reason": "model_unpinned"}
    if not question or question.get("distribution_complete") is not True:
        return {**verdict, "reason": "no_distribution"}
    supplied = answer.get("calibration")
    if supplied is not None:
        recorded = supplied.get("questions", {}).get(key) if isinstance(supplied, dict) and isinstance(supplied.get("questions"), dict) else None
        if (not isinstance(recorded, dict) or supplied.get("schema") != "carr.jev-calibration.v1"
                or supplied.get("model_answered") != model
                or supplied.get("model_pinned") is not True
                or supplied.get("model_requested") != model
                or not _distribution_matches(recorded.get("distribution"),
                                             question.get("distribution"))
                or any(recorded.get(field) != question.get(field) for field in
                       ("type", "distribution_complete", "probability_sum",
                        "entropy_bits", "top", "top_probability"))):
            return {**verdict, "reason": "calibration_mismatch"}
    if entropy is None:
        return {**verdict, "reason": "no_distribution"}
    if entropy > band["max_entropy_bits"]:
        return {**verdict, "reason": "above_calibrated_band"}
    return {**verdict, "route": "act", "reason": "within_calibrated_band"}


def agreement(log_path=SHADOW_LOG, kind=None):
    """Read the shadow log back: how often the judgment and the mechanism agree.

    This is the function that decides whether a caller may stop shadowing. It
    reports counts rather than a verdict, because "is this good enough to act
    on" depends on what acting costs and that is never this module's call.
    """
    rows = []
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if kind is None or row.get("kind") == kind:
                    rows.append(row)
    except OSError:
        return {"rows": 0, "errors": 0, "comparable": 0, "agreed": 0, "disagreed": 0}
    errors = sum(1 for row in rows if "error" in row)
    comparable = [row for row in rows
                  if "error" not in row and row.get("existing_decision") is not None]
    agreed = sum(1 for row in comparable if row.get("note") == "agreed")
    return {
        "rows": len(rows),
        "errors": errors,
        "comparable": len(comparable),
        "agreed": agreed,
        "disagreed": len(comparable) - agreed,
    }
