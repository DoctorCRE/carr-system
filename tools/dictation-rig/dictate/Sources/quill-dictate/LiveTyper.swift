// LiveTyper.swift — in-field revision engine for inline live-preview typing,
// plus the "scratch that" voice-command interpreter (both added 2026-08-08,
// replacing the floating PreviewOverlay panel Joe rejected: he wants the
// words IN the field, like typing, not a HUD beside it).
//
// The pipeline feeding this is unchanged from the panel days — the SAME
// resident whisper-server small.en polling from App.swift/PreviewServer.swift
// — only the destination of the text changed: instead of drawing a
// hypothesis in a panel, we synthesize it directly into whatever field is
// focused, revising it in place on every tick.
//
// Revision strategy: never edit mid-word. Each tick diffs the new
// hypothesis against what's already typed, then backs the diff point off to
// the start of the word it lands inside — replacing a whole word reads as a
// clean correction; a partial mid-word edit reads as a glitch. The
// backspace count is grapheme-cluster based (one visible character per
// backspace, matching what a human eye counts as "one character"); the
// retyped remainder is chunked by UTF-16 units — CGEvent's
// keyboardSetUnicodeString operates on UTF-16, not graphemes, so the two
// counts are kept deliberately separate. The chunking technique mirrors
// Inserter.typeOut (16 units/chunk, 4ms pacing) but isn't shared code with
// it: Inserter's version is an instance method tied to its own
// struct/config, and this path has none of its own.
//
// Every synthetic event carries Inserter.syntheticMarker so GestureEngine's
// tap ignores our own output — without it, our own backspaces/keystrokes
// would look like "another key pressed" and would abort the capture
// (GestureEngine.swift's cancelTriggerGesture fires on exactly that).
//

import AppKit
import CoreGraphics
import Foundation

final class LiveTyper {
    /// What we have synthetically typed into the focused field so far, for
    /// THIS capture. Reset by handing App.swift a fresh LiveTyper() at
    /// capture start; cleared to "" by finalize(with:) and retractAll().
    @MainActor private(set) var typed: String = ""

    #if LIVE_TYPER_CHECK
    enum CheckEvent: Equatable { case backspaces(Int), text(String) }
    @MainActor private let checkEmit: (CheckEvent) -> Void
    @MainActor init(checkEmit: @escaping (CheckEvent) -> Void) {
        self.checkEmit = checkEmit
    }
    #else
    init() {}
    #endif

    private static let backspaceKeyCode: CGKeyCode = 51

    /// Revise the field to match a new hypothesis: backspace the part of
    /// `typed` that no longer matches, then type the new remainder.
    @MainActor func apply(hypothesis: String) {
        guard hypothesis != typed else { return }
        let typedChars = Array(typed)
        let hypChars = Array(hypothesis)

        var rawL = 0
        while rawL < typedChars.count, rawL < hypChars.count, typedChars[rawL] == hypChars[rawL] {
            rawL += 1
        }

        var adjustedL = rawL
        if adjustedL > 0, adjustedL < typedChars.count,
           !typedChars[adjustedL - 1].isWhitespace, !typedChars[adjustedL].isWhitespace {
            // rawL lands inside a word that's about to change — back off to
            // that word's start so the revision replaces the whole word
            // instead of splicing its tail.
            while adjustedL > 0, !typedChars[adjustedL - 1].isWhitespace {
                adjustedL -= 1
            }
        }

        let backspaceCount = typedChars.count - adjustedL
        let remainder = String(hypChars[adjustedL...])

        if backspaceCount > 0 { postBackspaces(backspaceCount) }
        if !remainder.isEmpty { typeChunked(remainder) }

        typed = hypothesis
    }

    /// Called when the final (large-v3-turbo) pass returns: revise the
    /// field to hold `finalText` followed by any plain text the user appended
    /// after releasing the trigger, then clear state for the next capture.
    /// Treating that suffix as part of our current logical text lets the
    /// normal diff retract and replay it without losing the user's typing.
    @MainActor func finalize(with finalText: String, preservingAppended appendedText: String = "") {
        typed.append(appendedText)
        apply(hypothesis: finalText + appendedText)
        typed = ""
    }

    /// Backspace away everything typed this capture and reset. Used on
    /// abort, on a silence-gated or empty capture, and when whisper's final
    /// pass heard nothing.
    @MainActor func retractAll() {
        guard !typed.isEmpty else { return }
        postBackspaces(typed.count)
        typed = ""
    }

    /// Remove this capture's preview while preserving plain text appended
    /// after trigger release. Used when the final pass is empty or fails;
    /// the old bare retractAll() would backspace the user's new suffix first.
    @MainActor func retractAll(preservingAppended appendedText: String) {
        typed.append(appendedText)
        apply(hypothesis: appendedText)
        typed = ""
    }

    /// Backspace `count` characters WITHOUT touching `typed`. Used for the
    /// cross-utterance "scratch that" command (App.swift): that erases a
    /// PRIOR, already-completed insertion, not anything tracked by this
    /// capture's own `typed` state, so it must not go through the normal
    /// diff path.
    @MainActor func backspaceCharacters(_ count: Int) {
        guard count > 0 else { return }
        postBackspaces(count)
    }

    // MARK: - "scratch that" command (added 2026-08-08, Joe: "i want to
    // visibly see it be removed so that i know whats been erased")

    /// Case-insensitively finds the LAST "scratch that" in `text`,
    /// tolerating punctuation/commas immediately around it (whisper renders
    /// it as e.g. "..., scratch that, ..." or "Scratch that."). `kept` is
    /// everything after that occurrence, with leading whitespace/punctuation
    /// trimmed, then run through CorrectionResolver so a natural
    /// self-correction ("send it Tuesday, no wait, Wednesday") resolves
    /// before it ever reaches apply()/finalize() (added 2026-08-08).
    /// `scratchPrevious` is true only when the phrase was found AND nothing
    /// follows it — the whole utterance WAS the command; it's computed off
    /// the PRE-resolver `kept` since an empty string resolves to itself
    /// either way. A mid-utterance "X, scratch that, Y" needs no flag:
    /// `kept` == "Y" (resolved) is already the whole story, and running it
    /// through apply()/finalize() naturally backspaces X and types Y via the
    /// normal diff.
    static func interpret(_ text: String) -> (kept: String, scratchPrevious: Bool) {
        let result = interpretFinal(text)
        return (result.heuristic, result.scratchPrevious)
    }

    /// Same scratch-that stripping as interpret(_:), but surfaces BOTH the
    /// raw post-strip text and the heuristic (CorrectionResolver) resolution
    /// of it, instead of only the latter. Added 2026-08-08 for the local-LLM
    /// cleanup pass (App.swift's final path / main.swift's `cleanup`
    /// subcommand): the LLM needs the RAW text (a correction cue like "wait"
    /// might already be gone by the time CorrectionResolver is done with it)
    /// while the heuristic result remains the fallback when the LLM is
    /// skipped, times out, or its output fails the deletion-only guard.
    /// interpret(_:) above is a thin wrapper over this so the LIVE tick path
    /// (App.swift's previewTick, which calls interpret(_:) directly) is
    /// byte-identical in behavior to before this method existed.
    static func interpretFinal(_ text: String) -> (raw: String, heuristic: String, scratchPrevious: Bool) {
        guard let range = text.range(of: "scratch that", options: [.caseInsensitive, .backwards]) else {
            return (text, CorrectionResolver.resolve(text), false)
        }
        var rest = text[range.upperBound...]
        while let first = rest.first, first.isWhitespace || first.isPunctuation {
            rest = rest.dropFirst()
        }
        let kept = String(rest)
        return (kept, CorrectionResolver.resolve(kept), kept.isEmpty)
    }

    // MARK: - Correction-cue detection (local-LLM cleanup gate, 2026-08-08)

    /// Cues that make the raw (pre-heuristic) text worth sending to the local
    /// cleanup LLM at all — a natural self-correction almost always contains
    /// one of these, and gating on their presence keeps the fully-local fast
    /// path (no cue = no network call, however loopback-local) the common
    /// case instead of the exception. Case-insensitive substring match on the
    /// RAW text, deliberately not tokenized: "no," needs its comma to avoid
    /// firing on every bare "no", and substring matching is simpler and
    /// exactly as safe here since a false-positive cue only costs one wasted
    /// (fast, local) LLM round trip — it is the LLM's OWN output, not the
    /// cue match, that the deletion-only guard protects.
    private static let correctionCues = [
        "wait", "i mean", "not that", "no,", "make that", "strike that",
        "actually", "rather", "i meant",
    ]

    static func containsCorrectionCue(_ text: String) -> Bool {
        let lowered = text.lowercased()
        return correctionCues.contains { lowered.contains($0) }
    }

    // MARK: - Synthetic input

    @MainActor private func postBackspaces(_ count: Int) {
        #if LIVE_TYPER_CHECK
        checkEmit(.backspaces(count))
        #else
        let source = CGEventSource(stateID: .combinedSessionState)
        for _ in 0..<count {
            guard let down = CGEvent(keyboardEventSource: source, virtualKey: LiveTyper.backspaceKeyCode, keyDown: true),
                  let up = CGEvent(keyboardEventSource: source, virtualKey: LiveTyper.backspaceKeyCode, keyDown: false) else { continue }
            down.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
            up.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            usleep(2000)
        }
        #endif
    }

    @MainActor private func typeChunked(_ text: String) {
        #if LIVE_TYPER_CHECK
        checkEmit(.text(text))
        #else
        let source = CGEventSource(stateID: .combinedSessionState)
        // Chunked: keyboardSetUnicodeString tops out around 20 UTF-16 units
        // per event in practice (same limit Inserter.typeOut works around).
        let units = Array(text.utf16)
        var index = 0
        while index < units.count {
            let chunk = Array(units[index ..< min(index + 16, units.count)])
            if let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true) {
                down.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: chunk)
                down.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
                down.post(tap: .cghidEventTap)
            }
            if let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) {
                up.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
                up.post(tap: .cghidEventTap)
            }
            index += 16
            usleep(4000)
        }
        #endif
    }
}
