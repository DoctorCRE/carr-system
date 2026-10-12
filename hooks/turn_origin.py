#!/usr/bin/env python3
"""One answer to "did a partner actually type this", for every gate that asks.

WHY THIS FILE EXISTS. Two gates asked the same question and gave different
answers, and the wrong one shipped. ledger-sweep.py worked it out the expensive
way — patched four times in nine days as each new injected shape defeated it —
and ended at a POSITIVE test: is there evidence the partner typed this, with
anything else treated as not his. weekend-quiet-gate.py asked the same question
with a guessed allowlist of origin kinds, accepting "user" and "keyboard", which
the harness has never written. Measured across sixteen real transcripts, that
allowlist matched ZERO of 3,271 candidate records, so its carve-out — a partner
typing ends the weekend quiet — could not fire once (defect 16291f00).

The knowledge that would have prevented it was sitting in the sibling file the
whole time, in a field nobody had read. That is the shape rule a8c55a47 names: a
manual path and an automated path that do the same job must be the same code.
Two implementations of one question do not double the enforcement; they let the
weaker one be wrong on its own for a week.

WHAT THE CALLERS GET. is_harness_injected is lifted from ledger-sweep verbatim,
including the counted-transcript evidence in its docstring, because that
evidence is the argument for the design and rewriting it would lose the
provenance. partner_typed_this is the inverse, for callers that want the
positive form.

CHANGE THIS FILE AGAINST A TRANSCRIPT, NEVER AGAINST AN ASSUMPTION. Every
correction recorded below was made by counting records in a live session, and
two of them were corrections OF an earlier guess.
"""

from __future__ import annotations

NON_HUMAN_ORIGIN_KINDS = {"task-notification"}
# Third shape found live 2026-08-06, minutes after the first fix shipped: the
# autonomous-loop tick prompt ("# Autonomous loop check ... invoked on a
# timer") is injected AS a user message — origin-stamped like a human turn —
# so the kind=="human" early-return skipped the content check entirely and the
# sweep quoted the harness's own loop instructions as the partner's words.
# Content prefixes therefore run BEFORE the kind=="human" trust: a partner
# opening a typed message with one of these exact strings is vanishingly
# unlikely, and the observed failure (harness text wearing a human stamp) is
# real three times over.
NON_HUMAN_TEXT_PREFIXES = (
    "<task-notification", "<system-reminder", "[SYSTEM",
    "# Autonomous loop check", "<<autonomous-loop",
    "Another Claude session sent a message",
    # Fifth shape, counted 2026-10-12 from the Orchestrator.macbook transcript
    # (11 of them in one session): the COMPACTION SUMMARY the harness writes
    # when a context fills up. It is a `user` record carrying the model's own
    # recap of the session — standing rulings, bullet lists of what to do next —
    # stamped isCompactSummary and with turnOrigin human/peer/task_notification
    # at random, and with NO origin dict at all. The completion-evidence gate
    # read its bullets ("- **Release pipeline (ops/release-pipeline", "Merge only
    # after an independent review at head") as Joe's ORDERED CLAUSES and held
    # the turn open demanding production receipts for them, three times in one
    # day. The flag check in is_harness_injected is the discriminator; this
    # prefix is depth for a record that lost its flags on export.
    "This session is being continued from a previous conversation",
)

# Fourth shape, found live 2026-08-14: a message relayed from ANOTHER CLAUDE
# SESSION, whose body is a peer agent talking — the sweep quoted one back to Joe
# as "His words".
#
# CORRECTION, 2026-08-14, same day: this comment first said the relay "arrives
# wearing origin {kind: human}, the same disguise as the loop tick". That was
# reasoning by analogy and it was WRONG about the bytes. Counted from a live
# transcript, relays are stamped `origin: {"kind": "peer"}` with `isMeta` true —
# labelled honestly all along, in a field nobody had read. The content check
# below still earned its place (it is what caught the relay before the field was
# examined, and it covers records carrying no origin at all), but it is depth
# now, not the discriminator. The lesson is the one in is_harness_injected: the
# fix was patched onto the guess instead of onto the data.
#
# WHY A CONTAINMENT CHECK AND NOT JUST A PREFIX. The harness wraps the tag in a
# plain-English lead-in ("Another Claude session sent a message:\n<cross-session
# -message from=...>"), so the record does NOT begin with the tag and a
# startswith() on it alone sees nothing. The lead-in is in the prefix tuple
# above, but that sentence is harness wording and may be reworded; the TAG is
# the machine-readable part and is the thing worth keying on. Scanning only the
# head keeps a partner who genuinely quotes the tag deep in a long message from
# being misread as a relay.
PEER_RELAY_TAG = "<cross-session-message"
PEER_RELAY_SCAN_CHARS = 400


def is_harness_injected(rec, text):
    """True if `rec` was injected by the harness rather than typed by the partner.

    THIS IS A POSITIVE TEST WEARING A NEGATIVE NAME, and the inversion is the
    point. Every earlier version was a blocklist: it named the injected shapes it
    knew and TRUSTED EVERYTHING ELSE. That default is why this one hook was
    patched four times in nine days — task-notification (08-05), the autonomous
    loop tick (08-06), the cross-session relay (08-14) — each new shape defeating
    it once, in production, before anyone knew the shape existed. A blocklist
    cannot be finished, because the list of things the harness might inject next
    is not ours to enumerate.

    So the question is no longer "do I recognise this as injected" but "is there
    POSITIVE EVIDENCE the partner typed this". Anything else is not his.

    THE EVIDENCE, counted from a live transcript on 2026-08-14 rather than
    assumed — 421 user records in one session:
        origin.kind | promptSource | isMeta | count
        human       | sdk          | -      |  22   <- the partner, actually typing
        (none)      | -            | -      | 377   <- tool results, no text at all
        (none)      | -            | True   |  12   <- stop-hook feedback
        task-notif. | sdk          | -      |   5   <- a background agent finished
        peer        | sdk          | True   |   5   <- ANOTHER CLAUDE talking

    Two machine-set fields separate them, and neither is prose the harness might
    reword: `isMeta` is true on everything the harness inserts as commentary, and
    `origin.kind` is "human" only when a partner actually typed. Note the peer
    row — the relay was ALWAYS labelled "peer" in the bytes. Yesterday's fix
    guessed it wore a human stamp (by analogy with the loop tick, which does) and
    caught it by content instead. The content check stays as depth, but the
    labelled field is the honest discriminator and it was there all along,
    unread.

    The stop-hook row is the free catch: 12 records in one session that no
    content prefix ever matched, where the session's OWN gate talks in
    ruling-shaped language. Under the blocklist that was a false positive waiting
    for someone to notice.
    """
    # 1. HARNESS COMMENTARY. isMeta marks anything the harness inserted around
    #    the conversation rather than in it — peer relays, stop-hook feedback.
    #    Never set on a typed turn.
    if rec.get("isMeta"):
        return True
    # 1b. COMPACTION SUMMARIES. The harness marks the recap it injects after a
    #    context fills with isCompactSummary; it never sets origin on it, so
    #    the inversion in step 3 cannot see it, and it is the one injected
    #    record that is WRITTEN IN THE MODEL'S VOICE about the partner's orders.
    if rec.get("isCompactSummary"):
        return True
    # 2. CONTENT SHAPES, kept ahead of the origin check because the loop tick
    #    genuinely does arrive origin-stamped "human" and only its text betrays
    #    it. Depth behind the field checks, not the primary defence any more.
    if text and text.lstrip().startswith(NON_HUMAN_TEXT_PREFIXES):
        return True
    if text and PEER_RELAY_TAG in text[:PEER_RELAY_SCAN_CHARS]:
        return True
    origin = rec.get("origin")
    if isinstance(origin, dict):
        kind = origin.get("kind")
        # 3. THE INVERSION ITSELF. Previously: `if kind in NON_HUMAN_ORIGIN_KINDS`
        #    — a named list, so "peer" and every kind invented after this file was
        #    written fell through to trusted. Now anything that is not the one
        #    known-good value is not the partner. A future kind is quiet by
        #    default and gets a test when someone meets it, instead of a fabricated
        #    ledger entry in Joe's name and a fifth patch.
        if kind != "human":
            return True
        if kind == "human":
            return False
    return False


def partner_typed_this(rec, text):
    """The positive form: True when this record is a partner actually typing.

    A record with no text is never a keystroke — tool results arrive as user
    records carrying only tool_result blocks, and they are the single largest
    class in any transcript (377 of 421 in the counted session).
    """
    if not text or not text.strip():
        return False
    return not is_harness_injected(rec, text)
