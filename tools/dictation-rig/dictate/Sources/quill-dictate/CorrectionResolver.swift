// CorrectionResolver.swift — local heuristic resolution of Joe's natural
// self-corrections in dictated text (added 2026-08-08, extending the
// "scratch that" command LiveTyper.swift already handles). Joe speaks
// corrections the way people actually talk — "send it Tuesday, no wait,
// Wednesday", "copy Whitfeld, I mean Whitfield" — and the ruling on this
// (his words: "resolve these LOCALLY, no cloud, make it as good as
// possible") is a bias, not just a scope limit: a false positive here
// (mangling text that was never a correction) is WORSE than a false
// negative (leaving a real correction unresolved and typed verbatim), so
// every rule below is written to require unambiguous, verbatim evidence
// before it touches anything, and to leave text alone the moment that
// evidence is missing.
//
// Pure functions only — no side effects, no shared state — so the whole
// thing is testable from `quill-dictate interpret "<text>"` (main.swift)
// without a microphone, a field, or an accessibility grant in the loop.
//
// Implementation shape: text is tokenized once into words/punct/space
// (tokenize), the three rules each scan that token array left-to-right for
// their own pattern and, on the FIRST match, return a fully rebuilt token
// array (never mutate in place — a rule's own match indices are only valid
// against the array it was handed). resolve() reruns the rule chain against
// the latest array until nothing fires, capped at 8 passes so a pathological
// input can't loop.
import Foundation

enum CorrectionResolver {
    private enum TokenKind { case word, punct, space }

    private struct Token {
        var text: String
        var kind: TokenKind
    }

    private static let maxIterations = 8

    /// Replacement cues for RULE B — each a fixed two-word phrase, matched
    /// case-insensitively against adjacent word tokens (punctuation touching
    /// either side is tolerated because it lives in separate punct tokens).
    /// "make it" added 2026-08-08 (live failure round) alongside the comma
    /// gate below — see applyRuleB.
    private static let replacementCues: Set<[String]> = [
        ["i", "mean"], ["no", "wait"], ["oh", "wait"], ["wait", "no"],
        ["no", "no"], ["make", "that"], ["strike", "that"], ["make", "it"],
    ]

    /// FILLER ABSORPTION words (added 2026-08-08) — when one of these sits
    /// directly before a matched RULE B cue, it's folded into the cue's own
    /// span instead of being mistaken for part of the tail being replaced.
    /// See applyRuleB.
    private static let fillerWords: Set<String> = ["actually", "oh", "no", "wait", "sorry"]

    // MARK: - Public API

    static func resolve(_ text: String) -> String {
        guard !text.isEmpty else { return text }
        var tokens = tokenize(text)
        for _ in 0..<maxIterations {
            if let next = applyRuleA(tokens) { tokens = next; continue }
            if let next = applyRuleB(tokens) { tokens = next; continue }
            if let next = applyRuleC(tokens) { tokens = next; continue }
            break
        }
        return render(tokens)
    }

    // MARK: - Paraphrase firewall (added 2026-08-08 for the local-LLM
    // cleanup pass in CleanupServer.swift)
    //
    // Live testing showed the cleanup LLM sometimes paraphrases instead of
    // just deleting ("about" -> "regarding") — unacceptable for a dictation
    // tool, which must never reword the speaker. A true self-correction only
    // REMOVES words, so the LLM's output is trusted only when it reduces to
    // an ordered subsequence of its own input's words. Anything else —
    // insertion, substitution, reordering — fails this and the caller falls
    // back to the heuristic result. Deliberately conservative in the same
    // spirit as the rules above: candidate must be non-empty (an LLM that
    // "corrects" everything away is a bug, not a save) and every one of its
    // words must be found, in order, in source.
    /// Word-level difference test for arbitration. The renderer can make
    /// cosmetic spacing/punctuation changes without resolving anything, and a
    /// string-!= comparison read one of those as "the heuristic resolved
    /// something" — which then SKIPPED the LLM that would have caught the
    /// real correction (live, 2026-08-08). Same tokenization as the
    /// deletion-only guard, so "different" always means different WORDS.
    static func differsInWords(_ a: String, _ b: String) -> Bool {
        wordsOnly(a) != wordsOnly(b)
    }

    static func isDeletionOnly(candidate: String, of source: String) -> Bool {
        let candidateWords = wordsOnly(candidate)
        guard !candidateWords.isEmpty else { return false }
        let sourceWords = wordsOnly(source)

        var sourceIndex = 0
        for word in candidateWords {
            var matched = false
            while sourceIndex < sourceWords.count {
                let sourceWord = sourceWords[sourceIndex]
                sourceIndex += 1
                if sourceWord == word { matched = true; break }
            }
            guard matched else { return false }
        }
        return true
    }

    /// Lowercase words with punctuation stripped, split on whitespace — a
    /// deliberately cruder tokenizer than tokenize()/Token above: the guard
    /// only needs to compare WORD IDENTITY, not reproduce text, so there is
    /// no need to track punctuation or whitespace as their own tokens.
    private static func wordsOnly(_ text: String) -> [String] {
        text.lowercased()
            .split(whereSeparator: { $0.isWhitespace })
            .map { word in String(word.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }) }
            .filter { !$0.isEmpty }
    }

    // MARK: - RULE A: explicit "not X, Y" replacement
    //
    // "…X… , (no,)? not X, Y…" — X after "not" must reappear, verbatim, in
    // the 10 words before the cue, or this never fires (that's what keeps
    // "it's not urgent, call Monday" untouched: "urgent" isn't anywhere
    // earlier). On a hit, the whole span from the earlier X through the end
    // of Y is replaced by one copy of Y — this deliberately swallows
    // whatever sits between the earlier X and the cue (there's normally
    // nothing there; Joe's corrections land right after the value), rather
    // than trying to thread a needle the spec's own example doesn't need.
    //
    // SHARED-FIRST-WORD ANCHORING (fixed 2026-08-08, the same anchoring
    // Rule B already does for its own tail search): when Y's first word is
    // the same word sitting immediately before the earlier X occurrence,
    // deleting only from X forward leaves that shared word standing on both
    // sides of the splice — "send it at 3, not 3, at 4." resolved to "send
    // it at at 4." instead of "send it at 4." because "at" (Y's first word)
    // also immediately precedes the earlier "3" (X). Fixed below by
    // absorbing that shared word into the deleted span when it matches.

    private static func applyRuleA(_ tokens: [Token]) -> [Token]? {
        let wordIdxs = tokens.indices.filter { tokens[$0].kind == .word }
        for wp in 0..<wordIdxs.count {
            guard tokens[wordIdxs[wp]].text.lowercased() == "not" else { continue }
            var cueStartWP = wp
            if wp > 0, tokens[wordIdxs[wp - 1]].text.lowercased() == "no" {
                cueStartWP = wp - 1
            }

            guard let xResult = collectBoundedPhrase(startWP: wp + 1, maxWords: 4, wordIdxs: wordIdxs, tokens: tokens) else { continue }
            // X must end on a COMMA specifically — that's what makes this
            // "not X, Y" rather than an ordinary negated sentence.
            guard xResult.boundaryTokenIdx < tokens.count,
                  tokens[xResult.boundaryTokenIdx].kind == .punct,
                  tokens[xResult.boundaryTokenIdx].text.contains(",") else { continue }

            let yStartWP = xResult.wordPositions.last! + 1
            guard let yResult = collectBoundedPhrase(startWP: yStartWP, maxWords: 6, wordIdxs: wordIdxs, tokens: tokens) else { continue }

            let xCores = xResult.wordPositions.map { tokens[wordIdxs[$0]].text.lowercased() }
            let windowStart = max(0, cueStartWP - 10)
            let windowEnd = cueStartWP - 1
            guard windowEnd >= windowStart, xCores.count <= (windowEnd - windowStart + 1) else { continue }

            var matchStartWP: Int? = nil
            var searchWP = windowEnd - xCores.count + 1
            while searchWP >= windowStart {
                if (0..<xCores.count).allSatisfy({ tokens[wordIdxs[searchWP + $0]].text.lowercased() == xCores[$0] }) {
                    matchStartWP = searchWP
                    break
                }
                searchWP -= 1
            }
            guard let earlyWP = matchStartWP else { continue } // X never said earlier — leave the sentence alone

            // SHARED-FIRST-WORD ANCHORING: if the word right before the
            // earlier X is the same word Y starts with, absorb it into the
            // deleted span too — otherwise it survives as the tail of the
            // untouched prefix AND reappears as the head of the inserted Y,
            // duplicating it ("send it at 3, not 3, at 4." -> "send it at
            // at 4." without this check).
            var deleteStartWP = earlyWP
            if deleteStartWP > 0,
               let yFirstWord = yResult.wordPositions.first.map({ tokens[wordIdxs[$0]].text.lowercased() }),
               tokens[wordIdxs[deleteStartWP - 1]].text.lowercased() == yFirstWord {
                deleteStartWP -= 1
            }

            let deleteStartTokenIdx = wordIdxs[deleteStartWP]
            let capitalize = isSentenceStart(tokens: tokens, beforeTokenIdx: deleteStartTokenIdx)
            let replacement = replacementTokens(words: yResult.wordPositions.map { tokens[wordIdxs[$0]].text }, capitalize: capitalize)
            return spliceReplacement(tokens: tokens, deleteFrom: deleteStartTokenIdx, replacement: replacement, boundaryTokenIdx: yResult.boundaryTokenIdx)
        }
        return nil
    }

    // MARK: - RULE B: replacement cues ("i mean", "no wait", "make it", ...)
    //
    // ≤4 words after the cue: it's a correction — replace the same stretch
    // before the cue (anchored on a shared first word when one exists, per
    // the special case) with the words after, and drop the cue. >4 words
    // after the cue: it's a new thought, not a correction of the tail — the
    // cue phrase itself still must never survive into typed text, but
    // nothing else moves.
    //
    // Three refinements added 2026-08-08 after a live failure round (Joe:
    // "send the packet Tuesday, oh wait, actually make it Thursday" resolved
    // to "actually make it Thursday" instead of "send the packet Thursday"):
    //
    //   FILLER ABSORPTION — a contiguous run of up to 3 filler words
    //   (fillerWords) directly before the cue is folded into the cue's own
    //   span: it gets deleted with the cue, and the tail search below
    //   anchors on `anchorWP` (before the filler run) instead of
    //   `cueStartWP` (the cue itself). Without this, "actually" reads as
    //   part of the value being replaced instead of noise before the cue.
    //
    //   COMMA GATE, "make"-cues only ("make it", "make that") — fires only
    //   when the nearest non-space token before the (filler-adjusted) cue is
    //   a comma. A correction follows a spoken pause, which whisper renders
    //   as a comma ("Tuesday, make it Thursday"); ordinary usage doesn't
    //   ("we can make it work" must never fire). Other cues ("i mean", "no
    //   wait", ...) don't need this — their own wording is unambiguous.
    //
    //   CUE DEFERENCE — when what follows the cue itself starts a new
    //   correction (begins with "actually", or its own first two words are
    //   themselves a replacement cue), this cue is not yet the final word.
    //   Resolving now would consume the wrong span, so this pass strips only
    //   the cue phrase and returns, letting resolve()'s loop re-run RULE B
    //   on the remainder — where the inner cue gets its own, correct pass.
    private static func applyRuleB(_ tokens: [Token]) -> [Token]? {
        let wordIdxs = tokens.indices.filter { tokens[$0].kind == .word }
        guard wordIdxs.count >= 2 else { return nil }
        for wp in 0..<(wordIdxs.count - 1) {
            let pair = [tokens[wordIdxs[wp]].text.lowercased(), tokens[wordIdxs[wp + 1]].text.lowercased()]
            guard replacementCues.contains(pair) else { continue }
            let cueStartWP = wp
            let cueEndWP = wp + 1

            // FILLER ABSORPTION: walk back over up to 3 contiguous filler
            // words; anchorWP is where the cue's span REALLY starts for tail
            // search and the comma gate alike.
            var anchorWP = cueStartWP
            var fillerCount = 0
            while fillerCount < 3, anchorWP > 0,
                  fillerWords.contains(tokens[wordIdxs[anchorWP - 1]].text.lowercased()) {
                anchorWP -= 1
                fillerCount += 1
            }

            // COMMA GATE: make-cues only.
            if pair[0] == "make", !hasPrecedingComma(tokens: tokens, beforeTokenIdx: wordIdxs[anchorWP]) {
                continue // ordinary usage ("we can make it work") — keep scanning
            }

            guard let afterResult = collectBoundedPhrase(startWP: cueEndWP + 1, maxWords: 999, wordIdxs: wordIdxs, tokens: tokens) else { continue }
            let afterCount = afterResult.wordPositions.count

            // CUE DEFERENCE: let an inner correction resolve on its own pass.
            if afterCount >= 1 {
                let afterFirstWord = tokens[wordIdxs[afterResult.wordPositions[0]]].text.lowercased()
                var afterStartsWithCue = false
                if afterCount >= 2 {
                    let afterFirstTwo = [afterFirstWord, tokens[wordIdxs[afterResult.wordPositions[1]]].text.lowercased()]
                    afterStartsWithCue = replacementCues.contains(afterFirstTwo)
                }
                if afterFirstWord == "actually" || afterStartsWithCue {
                    return stripCuePhrase(tokens: tokens, cueStartWP: cueStartWP, cueEndWP: cueEndWP, wordIdxs: wordIdxs)
                }
            }

            if afterCount >= 1, afterCount <= 4 {
                let afterFirstCore = tokens[wordIdxs[afterResult.wordPositions[0]]].text.lowercased()
                var tailStartWP: Int?
                let lookbackFloor = max(0, anchorWP - 10)
                var s = anchorWP - 1
                while s >= lookbackFloor {
                    if tokens[wordIdxs[s]].text.lowercased() == afterFirstCore { tailStartWP = s; break }
                    s -= 1
                }
                if tailStartWP == nil {
                    let defaultStart = anchorWP - afterCount
                    if defaultStart >= 0 { tailStartWP = defaultStart }
                }
                guard let tStartWP = tailStartWP else { continue }

                let tailStartTokenIdx = wordIdxs[tStartWP]
                let capitalize = isSentenceStart(tokens: tokens, beforeTokenIdx: tailStartTokenIdx)
                let replacement = replacementTokens(words: afterResult.wordPositions.map { tokens[wordIdxs[$0]].text }, capitalize: capitalize)
                return spliceReplacement(tokens: tokens, deleteFrom: tailStartTokenIdx, replacement: replacement, boundaryTokenIdx: afterResult.boundaryTokenIdx)
            } else if afterCount > 4 {
                return stripCuePhrase(tokens: tokens, cueStartWP: cueStartWP, cueEndWP: cueEndWP, wordIdxs: wordIdxs)
            }
            // afterCount == 0 (cue with nothing following it, e.g. a capture
            // cut off mid-thought): unknown intent — leave it, keep scanning.
        }
        return nil
    }

    /// Deletes just the cue phrase (cueStartWP...cueEndWP), absorbing one
    /// touching punct token on either side (typically the trailing comma —
    /// whisper attaches punctuation directly to the preceding word, so a
    /// leading comma usually sits behind a space token and is left alone,
    /// which is what keeps a pause-comma before the cue intact for a later
    /// pass — see CUE DEFERENCE above). Shared by the >4-words "new thought"
    /// case and CUE DEFERENCE, which both want the same "cue vanishes,
    /// nothing else moves" behavior.
    private static func stripCuePhrase(tokens: [Token], cueStartWP: Int, cueEndWP: Int, wordIdxs: [Int]) -> [Token] {
        let cueStartTokenIdx = wordIdxs[cueStartWP]
        let cueEndTokenIdx = wordIdxs[cueEndWP]
        var deleteStart = cueStartTokenIdx
        if deleteStart > 0, tokens[deleteStart - 1].kind == .punct {
            deleteStart -= 1
        }
        var deleteEndExclusive = cueEndTokenIdx + 1
        if deleteEndExclusive < tokens.count, tokens[deleteEndExclusive].kind == .punct {
            deleteEndExclusive += 1
        }
        var newTokens = Array(tokens[0..<deleteStart])
        newTokens.append(Token(text: " ", kind: .space))
        if deleteEndExclusive < tokens.count { newTokens.append(contentsOf: tokens[deleteEndExclusive...]) }
        return newTokens
    }

    /// True when the nearest non-space token before `beforeTokenIdx` is a
    /// PAUSE mark — comma, period, question/exclamation mark, dash, ellipsis
    /// — the gate for make-cues. Whisper renders the spoken pause before a
    /// correction as whatever punctuation its language model prefers: live
    /// 2026-08-08, "Send the packet Tuesday. actually, make it Thursday."
    /// arrived with a PERIOD, and a comma-only gate silently declined the
    /// correction. A sentence break is stronger pause evidence than a comma,
    /// not weaker. Ordinary mid-clause "make it" ("we can make it work") has
    /// NO punctuation there and still never fires. Mirrors isSentenceStart's
    /// "skip spaces, then look at the one token that matters" shape.
    private static func hasPrecedingComma(tokens: [Token], beforeTokenIdx: Int) -> Bool {
        var i = beforeTokenIdx - 1
        while i >= 0, tokens[i].kind == .space { i -= 1 }
        guard i >= 0, tokens[i].kind == .punct else { return false }
        return tokens[i].text.rangeOfCharacter(from: CharacterSet(charactersIn: ",.?!—–…;:")) != nil
    }

    // MARK: - RULE C: stutter / restart dedup
    //
    // "send the, send the invoice" → "send the invoice". Longest phrase
    // (5 words) tried before shorter ones at each start position so a long
    // stutter isn't caught as a shorter false match; single-word repeats
    // ("that that") are never attempted since phraseLen starts at 2.

    private static func applyRuleC(_ tokens: [Token]) -> [Token]? {
        let wordIdxs = tokens.indices.filter { tokens[$0].kind == .word }
        let n = wordIdxs.count
        var wp = 0
        while wp < n {
            for phraseLen in stride(from: 5, through: 2, by: -1) {
                guard wp + 2 * phraseLen <= n else { continue }
                let equal = (0..<phraseLen).allSatisfy {
                    tokens[wordIdxs[wp + $0]].text.lowercased() == tokens[wordIdxs[wp + phraseLen + $0]].text.lowercased()
                }
                guard equal else { continue }

                let lastTok1 = wordIdxs[wp + phraseLen - 1]
                let firstTok2 = wordIdxs[wp + phraseLen]
                let between = tokens[(lastTok1 + 1)..<firstTok2]
                let punctBetween = between.filter { $0.kind == .punct }
                let nonComma = punctBetween.filter { !$0.text.contains(",") }
                guard nonComma.isEmpty, punctBetween.count <= 1 else { continue }

                let startDelete = wordIdxs[wp]
                var newTokens = Array(tokens[0..<startDelete])
                newTokens.append(contentsOf: tokens[firstTok2...])
                return newTokens
            }
            wp += 1
        }
        return nil
    }

    // MARK: - Shared span-replacement mechanics (rules A and B are the same
    // shape: delete from some start token through the end of a "words"
    // phrase, insert that phrase once, keep whatever punctuation ended it)

    private static func spliceReplacement(tokens: [Token], deleteFrom startTokenIdx: Int, replacement: [Token], boundaryTokenIdx: Int) -> [Token] {
        var terminator: [Token] = []
        var afterIdx = boundaryTokenIdx
        if boundaryTokenIdx < tokens.count, tokens[boundaryTokenIdx].kind == .punct {
            terminator = [tokens[boundaryTokenIdx]]
            afterIdx = boundaryTokenIdx + 1
        }
        var newTokens = Array(tokens[0..<startTokenIdx])
        newTokens.append(contentsOf: replacement)
        newTokens.append(contentsOf: terminator)
        if afterIdx < tokens.count { newTokens.append(contentsOf: tokens[afterIdx...]) }
        return newTokens
    }

    private static func replacementTokens(words: [String], capitalize: Bool) -> [Token] {
        var texts = words
        if capitalize, let first = texts.first, let firstChar = first.first {
            texts[0] = firstChar.uppercased() + first.dropFirst()
        }
        var out: [Token] = []
        for (i, text) in texts.enumerated() {
            if i > 0 { out.append(Token(text: " ", kind: .space)) }
            out.append(Token(text: text, kind: .word))
        }
        return out
    }

    /// True when `beforeTokenIdx` sits at the very start of the text, or
    /// right after sentence-ending punctuation (skipping whitespace) — the
    /// only cases where a dropped-in replacement needs re-capitalizing.
    private static func isSentenceStart(tokens: [Token], beforeTokenIdx: Int) -> Bool {
        var i = beforeTokenIdx - 1
        while i >= 0, tokens[i].kind == .space { i -= 1 }
        guard i >= 0 else { return true }
        guard tokens[i].kind == .punct else { return false }
        return tokens[i].text.contains(".") || tokens[i].text.contains("!") || tokens[i].text.contains("?")
    }

    /// Collects consecutive word tokens starting at word-position `startWP`,
    /// stopping the instant the word just added is followed by a punct
    /// token (that punct is the "boundary") or by the end of the text.
    /// Returns nil if the phrase would need more than `maxWords` to reach a
    /// boundary, or if there's no word at `startWP` at all — both signal
    /// "this isn't a clean bounded phrase," which every call site treats as
    /// "don't fire."
    private static func collectBoundedPhrase(startWP: Int, maxWords: Int, wordIdxs: [Int], tokens: [Token]) -> (wordPositions: [Int], boundaryTokenIdx: Int)? {
        guard startWP < wordIdxs.count else { return nil }
        var positions: [Int] = []
        var wp = startWP
        while true {
            positions.append(wp)
            if positions.count > maxWords { return nil }
            let tokenIdx = wordIdxs[wp]
            let nextIdx = tokenIdx + 1
            if nextIdx >= tokens.count { return (positions, tokens.count) }
            if tokens[nextIdx].kind == .punct { return (positions, nextIdx) }
            wp += 1
            if wp >= wordIdxs.count { return (positions, tokens.count) }
        }
    }

    // MARK: - Tokenizing / rendering

    /// Splits into words (letters/digits/apostrophe/hyphen), punctuation
    /// (everything else non-whitespace, run-grouped), and whitespace
    /// (run-grouped). Kept deliberately dumb — this only has to round-trip
    /// whisper's own output, not arbitrary text — so every rule above can
    /// reason in terms of "words" and "the punctuation right next to them"
    /// without re-deriving that from raw characters each time.
    private static func tokenize(_ text: String) -> [Token] {
        var tokens: [Token] = []
        var current = ""
        var currentKind: TokenKind?
        func flush() {
            if let kind = currentKind, !current.isEmpty {
                tokens.append(Token(text: current, kind: kind))
            }
            current = ""
            currentKind = nil
        }
        for ch in text {
            let kind: TokenKind
            if ch.isWhitespace { kind = .space }
            else if ch.isLetter || ch.isNumber || ch == "'" || ch == "-" { kind = .word }
            else { kind = .punct }
            if kind == currentKind {
                current.append(ch)
            } else {
                flush()
                current = String(ch)
                currentKind = kind
            }
        }
        flush()
        return tokens
    }

    /// Reassembles tokens and cleans up the only artifacts splicing can
    /// leave behind: doubled whitespace, and a stray space landing before
    /// punctuation.
    private static func render(_ tokens: [Token]) -> String {
        var s = tokens.map { $0.text }.joined()
        while s.contains("  ") { s = s.replacingOccurrences(of: "  ", with: " ") }
        for mark in [",", ".", "!", "?", ";", ":"] {
            s = s.replacingOccurrences(of: " \(mark)", with: mark)
        }
        return s.trimmingCharacters(in: .whitespaces)
    }
}
