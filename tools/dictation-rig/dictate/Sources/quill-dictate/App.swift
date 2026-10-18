// App.swift — menu-bar shell wiring gestures -> recorder -> whisper -> insert.
//
// Visibly distinct from meeting mode on purpose (loop #243's boundary): quill
// meeting mode has its own menu-bar item and an audible consent announcement;
// quill-dictate shows a keyboard-badge mic icon, plays only local UI cues, and
// records nothing except while a key is physically held.

import AppKit
import AVFoundation
import Foundation
import QuillActivity

final class AppDelegate: NSObject, NSApplicationDelegate, GestureDelegate {
    private var config = Config.load()
    private var statusItem: NSStatusItem!
    private var gestures: GestureEngine!
    private let recorder = Recorder()
    private let workQueue = DispatchQueue(label: "quill-dictate.pipeline", qos: .userInitiated)
    private var axPollTimer: Timer?

    // Live-transcription preview (added 2026-08-08; converted from a floating
    // panel to inline live typing same day — Joe rejected the panel, wants
    // words to appear IN the field like typing). Entirely separate from the
    // recorder/transcriber/inserter pipeline above — see WhisperServer.swift,
    // PreviewOverlay.swift, and LiveTyper.swift for why this must never be
    // able to block or delay a real capture.
    private var previewServer: WhisperServer?
    private let previewOverlay = PreviewOverlay()

    /// Resident FINAL-pass whisper server (added 2026-08-08), spawned only
    /// when final_server == "auto". A FOURTH resident process alongside
    /// recorder/whisper-cli, previewServer's whisper-server, and
    /// cleanupServer's llama-server — same lifecycle shape (spawn at
    /// launch, stop at terminate), different port and model (the existing
    /// model_path, large-v3-turbo). Transcriber.swift talks to it directly
    /// over HTTP rather than through this instance — see that file's header
    /// — so this property exists purely to own the child process's
    /// lifecycle, exactly like previewServer and cleanupServer do for theirs.
    private var finalServer: WhisperServer?

    /// Local-LLM self-correction cleanup pass (added 2026-08-08). A THIRD
    /// resident process alongside recorder/whisper-cli and PreviewServer's
    /// whisper-server, spawned only when correction_llm == "auto". Touched
    /// only from the FINAL transcription path (gestureCaptureEnded) — the
    /// live tick path (previewTick) never references this, so it can never
    /// add LLM latency to inline typing. See CleanupServer.swift.
    private var cleanupServer: CleanupServer?
    /// Rebuilt fresh at the start of every capture (startPreview) when
    /// preview_style is "inline" — see LiveTyper.swift for why a fresh
    /// instance rather than a reset() call.
    private var liveTyper = LiveTyper()
    private var previewTimer: Timer?
    /// Bumped every capture start; tags each in-flight preview request so a
    /// slow response that lands after the NEXT capture already started (or
    /// after capture ended) is dropped instead of showing stale text (or,
    /// for the inline style, typing stale text into the field after
    /// finalize already ran — see gestureCaptureEnded).
    private var previewGeneration = 0

    /// The last text this app actually landed in a field — finalize() or
    /// paste alike — so a standalone "scratch that" utterance (the whole
    /// utterance being the command, nothing to keep) can visibly erase it.
    /// Added 2026-08-08 per Joe: "i want to visibly see it be removed so
    /// that i know whats been erased." Scoped to app identity + a 5-minute
    /// staleness window so it can't reach across into an unrelated field or
    /// an unrelated, long-past dictation.
    private struct LastInsert { let text: String; let bundleId: String?; let timestamp: Date }
    private var lastInsert: LastInsert?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Log.shared.path = config.logPath
        Log.shared.line("START quill-dictate pid=\(ProcessInfo.processInfo.processIdentifier) trigger_keys=\(config.triggerKeyCodes)")

        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        buildMenu()
        setIcon(state: .waitingPermission)

        requestMicAccess()
        startWhenTrusted()

        if config.previewStyle == "inline" || config.previewStyle == "panel" {
            let server = WhisperServer(role: .preview, config: config)
            server.start()
            previewServer = server
        }

        if config.correctionLLM == "auto" {
            let cleanup = CleanupServer(config: config)
            cleanup.start()
            cleanupServer = cleanup
        }

        if config.finalServer == "auto" {
            let final = WhisperServer(role: .final, config: config)
            final.start()
            finalServer = final
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        previewServer?.stop()
        cleanupServer?.stop()
        finalServer?.stop()
    }

    // MARK: - Permissions

    private func requestMicAccess() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            break
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                Log.shared.line("INFO mic permission \(granted ? "granted" : "DENIED")")
            }
        default:
            Log.shared.line("WARN mic permission denied/restricted — dictation cannot capture")
        }
    }

    private func startWhenTrusted() {
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        if AXIsProcessTrustedWithOptions(options) {
            startTap()
            return
        }
        Log.shared.line("WAIT accessibility permission not granted yet — prompted; polling")
        axPollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] timer in
            guard let self, AXIsProcessTrusted() else { return }
            timer.invalidate()
            self.axPollTimer = nil
            self.startTap()
        }
    }

    private func startTap() {
        gestures = GestureEngine(config: config)
        gestures.delegate = self
        if gestures.start() {
            Log.shared.line("READY event tap live; hold right-cmd to talk, double-tap for conversation mode")
            setIcon(state: .idle)
        } else {
            Log.shared.line("ERROR event tap creation failed even though AX is trusted")
            setIcon(state: .error)
        }
    }

    // MARK: - GestureDelegate

    func gestureCaptureStarted(kind: GestureEngine.CaptureKind) {
        do {
            try recorder.start()
            setIcon(state: .recording)
            cue(config.soundCaptureStart)
            startPreview()
        } catch {
            Log.shared.line("ERROR mic start failed: \(error)")
            cue(config.soundError)
            setIcon(state: gestures.conversationMode ? .conversation : .idle)
        }
    }

    func gestureCaptureEnded() {
        stopPreview()
        guard recorder.isRecording else { return }
        let peak = recorder.peakLevel
        let seconds = recorder.capturedSeconds
        let wav = recorder.stop(writeTo: config.workDir)
        setIcon(state: .busy)

        guard let wav else {
            Log.shared.line("INFO empty capture, nothing to transcribe")
            MainActor.assumeIsolated { liveTyper.retractAll() } // defensive: near-instant release, but never leave live-typed text stranded
            settle()
            return
        }
        // Silence gate: a keypress with no real speech behind it inserts
        // nothing, instead of whisper hallucinating a caption onto noise.
        guard peak >= config.minPeakLevel, seconds >= 0.35 else {
            Log.shared.line("INFO capture gated (peak=\(String(format: "%.4f", peak)) seconds=\(String(format: "%.2f", seconds))) — discarded")
            try? FileManager.default.removeItem(at: wav)
            MainActor.assumeIsolated { liveTyper.retractAll() }
            cue(config.soundGated)
            settle()
            return
        }

        gestures.busy = true
        let transcriber = Transcriber(config: config, finalServer: finalServer)
        let inserter = Inserter(config: config)
        // Start before dispatch so keys typed immediately after trigger
        // release are captured. The journal preserves ordinary appended
        // text and marks focus/caret-changing activity unsafe.
        let postReleaseToken = gestures.beginPostReleaseTracking()
        workQueue.async { [weak self] in
            defer {
                DispatchQueue.main.async {
                    self?.gestures.busy = false
                    self?.settle()
                }
            }
            do {
                let started = Date()
                let text = try transcriber.transcribe(wav: wav)
                let elapsedSeconds = Date().timeIntervalSince(started)
                let elapsed = String(format: "%.1f", elapsedSeconds)
                try? FileManager.default.removeItem(at: wav)
                guard !text.isEmpty else {
                    Log.shared.line("INFO whisper heard nothing (\(elapsed)s)")
                    DispatchQueue.main.async {
                        guard let self else { return }
                        let activity = self.gestures.finishPostReleaseTracking(token: postReleaseToken)
                        if activity.isSafe {
                            self.liveTyper.retractAll(preservingAppended: activity.appendedText)
                        } else {
                            Log.shared.line("INFO empty final left live text untouched (\(activity.unsafeReason ?? "unsafe activity"))")
                            self.lastInsert = nil
                        }
                        self.cue(self.config.soundGated)
                    }
                    return
                }

                // "scratch that" (added 2026-08-08): strip everything up to
                // and including its last occurrence before this text ever
                // reaches a field. When the WHOLE utterance was the command
                // (nothing left after it), this isn't a normal insert at
                // all — it means "erase what I dictated last time," handled
                // separately below and never through the gated-cue path.
                // interpretFinal (vs. interpret, which previewTick's LIVE
                // tick path still calls directly and unchanged) additionally
                // surfaces the RAW post-strip text for the LLM cleanup pass
                // below — see LiveTyper.swift.
                let interpreted = LiveTyper.interpretFinal(text)
                if interpreted.scratchPrevious {
                    Log.shared.line("INFO scratch-that command received (\(elapsed)s)")
                    DispatchQueue.main.async {
                        guard let self else { return }
                        let activity = self.gestures.finishPostReleaseTracking(token: postReleaseToken)
                        guard activity.isSafe, activity.appendedText.isEmpty else {
                            Log.shared.line("INFO scratch-that skipped after post-release activity — field left untouched")
                            self.lastInsert = nil
                            return
                        }
                        self.liveTyper.retractAll()
                        self.handleCrossUtteranceScratch()
                    }
                    return
                }

                // Local-LLM cleanup (added 2026-08-08, FINAL path only): the
                // heuristic resolution is always the fallback; the LLM only
                // gets a turn when it's enabled, its server is up, and the
                // RAW text contains a correction cue a plain heuristic might
                // miss. No cue means no network call at all — plain
                // dictation never pays for this. Blocking is safe here: this
                // closure already runs on workQueue, off the main thread, so
                // waiting up to 1.5s here never touches UI responsiveness.
                let kept = self?.resolveFinalText(raw: interpreted.raw, heuristic: interpreted.heuristic) ?? interpreted.heuristic

                Log.shared.line("INSERT \(kept.count) chars in \(elapsed)s (audio \(String(format: "%.1f", seconds))s)")
                DispatchQueue.main.async {
                    guard let self else { return }

                    let activity = self.gestures.finishPostReleaseTracking(token: postReleaseToken)
                    guard activity.isSafe else {
                        Log.shared.line("INSERT skipped after \(activity.keyDowns) user keystrokes (\(activity.unsafeReason ?? "unsafe activity")) — field left untouched")
                        // The current caret is no longer known to sit directly
                        // after the dictation, so cross-utterance scratch is
                        // unsafe too.
                        self.lastInsert = nil
                        return
                    }

                    // FIX 4 (2026-08-08, same live failure round): a
                    // cold-start final pass arriving this late has likely
                    // landed well after Joe read and moved on from the
                    // live-typed text — rewriting the field now would
                    // overwrite text he's already acted on. Only guards the
                    // inline-with-live-text case; the plain paste path (no
                    // live text was ever typed) has nothing to protect and
                    // always inserts, regardless of elapsed time.
                    if self.config.previewStyle == "inline", !self.liveTyper.typed.isEmpty, elapsedSeconds > 10.0 {
                        Log.shared.line("INSERT slow final (\(elapsed)s) — live text stands")
                        if activity.appendedText.isEmpty {
                            self.recordLastInsert(self.liveTyper.typed)
                        } else {
                            self.lastInsert = nil
                        }
                        return
                    }

                    if !activity.appendedText.isEmpty,
                       self.config.previewStyle == "inline", !self.liveTyper.typed.isEmpty {
                        // Plain text was appended at the same caret while the
                        // final ran. Adopt it into LiveTyper's logical state,
                        // revise the dictation, then replay the suffix.
                        self.liveTyper.finalize(with: kept, preservingAppended: activity.appendedText)
                        Log.shared.line("INSERT preserved \(activity.appendedText.count) user-typed chars after final")
                        // The dictated text is now before a user-owned suffix,
                        // so a later blind backspace-based scratch cannot
                        // safely target it.
                        self.lastInsert = nil
                    } else if !activity.appendedText.isEmpty {
                        // Without inline preview text there is no field anchor
                        // proving where the dictation belongs. Keep the user's
                        // suffix and retain the original stale-result safety.
                        Log.shared.line("INSERT skipped after \(activity.keyDowns) appended keystrokes (no inline anchor) — field left untouched")
                        self.lastInsert = nil
                        return
                    } else if self.config.previewStyle == "inline", !self.liveTyper.typed.isEmpty {
                        // Something was already live-typed this capture —
                        // reconcile it against the more-accurate final pass
                        // instead of pasting a duplicate on top of it.
                        self.liveTyper.finalize(with: kept)
                    } else {
                        // Nothing was preview-typed (server down, zero ticks
                        // fired, or style is "panel"/"off") — fall back to
                        // the ordinary paste path exactly as before inline
                        // typing existed.
                        self.liveTyper.retractAll() // no-op unless style is inline and something partial slipped through
                        inserter.insert(kept)
                    }
                    if activity.appendedText.isEmpty {
                        self.recordLastInsert(kept)
                    }
                }
            } catch {
                Log.shared.line("ERROR transcription failed: \(error)")
                DispatchQueue.main.async {
                    guard let self else { return }
                    let activity = self.gestures.finishPostReleaseTracking(token: postReleaseToken)
                    if activity.isSafe {
                        self.liveTyper.retractAll(preservingAppended: activity.appendedText)
                    } else {
                        Log.shared.line("INFO failed final left live text untouched (\(activity.unsafeReason ?? "unsafe activity"))")
                        self.lastInsert = nil
                    }
                    self.cue(self.config.soundError)
                }
            }
        }
    }

    func gestureCaptureAborted() {
        stopPreview()
        recorder.abort()
        MainActor.assumeIsolated { liveTyper.retractAll() }
        Log.shared.line("INFO capture aborted (shortcut or mode exit)")
        settle()
    }

    /// Records what actually landed in a field — inline finalize or plain
    /// paste alike — as the target for a possible standalone "scratch that"
    /// on the NEXT capture. Called once per completed insertion.
    private func recordLastInsert(_ text: String) {
        lastInsert = LastInsert(text: text,
                                 bundleId: NSWorkspace.shared.frontmostApplication?.bundleIdentifier,
                                 timestamp: Date())
    }

    /// The whole utterance was "scratch that" — erase the PRIOR completed
    /// insertion (not anything from this capture, which live-typed nothing
    /// since the command was the entire utterance). Works regardless of
    /// preview_style: visible removal is the point, so even "panel"/"off"
    /// get it via LiveTyper's bare backspace primitive. Guarded by app
    /// identity + a 5-minute staleness window so it can never reach into an
    /// unrelated field or an unrelated, long-past dictation.
    @MainActor private func handleCrossUtteranceScratch() {
        guard let last = lastInsert else {
            Log.shared.line("INFO scratch-that: nothing to erase (no prior insert this session)")
            return
        }
        let currentBundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        let ageSeconds = Date().timeIntervalSince(last.timestamp)
        guard currentBundle == last.bundleId, ageSeconds < 300 else {
            Log.shared.line("INFO scratch-that: not erasing (app=\(currentBundle ?? "nil") last=\(last.bundleId ?? "nil") age=\(Int(ageSeconds))s)")
            return
        }
        liveTyper.backspaceCharacters(last.text.count)
        lastInsert = nil
        Log.shared.line("INFO scratch-that: erased previous insert (\(last.text.count) chars)")
    }

    // MARK: - Local-LLM cleanup decision (added 2026-08-08)

    /// Decide the text that should actually be inserted for the FINAL
    /// transcription. ARBITRATION FLIP (2026-08-08, live failure round):
    /// heuristics are now PRIMARY. If CorrectionResolver's rule chain
    /// resolved something (`heuristic != raw`), that result wins outright
    /// and the LLM is never consulted — live testing showed the LLM can
    /// delete the wrong half of a legal (guard-passing) correction ("send
    /// the packet Tuesday, oh wait, actually make it Thursday" -> "actually
    /// make it Thursday") while the heuristic gets it right. Only when the
    /// heuristic found NOTHING (`heuristic == raw`) and a correction cue is
    /// present in `raw` does the LLM get a turn — same deletion-only guard,
    /// same no-op demotion as before, except an LLM echo now falls back to
    /// `raw` (which, in this branch, IS `heuristic`). BLOCKING — callers
    /// must be off the main thread (this runs from workQueue, same as the
    /// rest of the final-transcription pipeline) — and bounded to the LLM's
    /// own 1.5s request timeout, so it can never hang the pipeline past
    /// that.
    private func resolveFinalText(raw: String, heuristic: String) -> String {
        // Word-level, not string-level: cosmetic re-rendering must not count
        // as "resolved" and block the LLM (live failure, 2026-08-08).
        guard !CorrectionResolver.differsInWords(heuristic, raw) else {
            Log.shared.line("CLEANUP heuristic resolved")
            return heuristic
        }

        guard config.correctionLLM == "auto",
              let cleanupServer, cleanupServer.isSpawned,
              LiveTyper.containsCorrectionCue(raw) else {
            return heuristic
        }

        let semaphore = DispatchSemaphore(value: 0)
        var llmResult: String?
        cleanupServer.cleanup(text: raw) { result in
            llmResult = result
            semaphore.signal()
        }
        _ = semaphore.wait(timeout: .now() + 1.5)

        guard let candidate = llmResult, !candidate.isEmpty else {
            Log.shared.line("CLEANUP timeout")
            return heuristic
        }
        guard CorrectionResolver.isDeletionOnly(candidate: candidate, of: raw) else {
            Log.shared.line("CLEANUP guard rejected llm (not an ordered deletion-only subsequence of the source)")
            return heuristic
        }
        // An unchanged echo is the model PUNTING, not resolving — it passes
        // the deletion-only guard trivially, and must not outrank raw (the
        // heuristic already found nothing in this branch, so raw IS the
        // heuristic result here). First live test round, 2026-08-08: the
        // 1.5B echoed "send it Tuesday, no wait, Wednesday" back verbatim.
        if CorrectionResolver.isDeletionOnly(candidate: raw, of: candidate) {
            Log.shared.line("CLEANUP llm no-op")
            return heuristic
        }
        Log.shared.line("CLEANUP llm won")
        return candidate
    }

    // MARK: - Live preview (additive; never gates the real pipeline)

    private func startPreview() {
        guard let previewServer, previewServer.isSpawned,
              config.previewStyle == "inline" || config.previewStyle == "panel" else { return }
        previewGeneration += 1
        let generation = previewGeneration

        switch config.previewStyle {
        case "inline":
            // Fresh typing state for this capture; no overlay in this style
            // — the words go straight into the field instead.
            liveTyper = LiveTyper()
        case "panel":
            // Caret lookup (2026-08-08) runs off-main: AX calls can stall in
            // some apps, and this path sits downstream of the event tap, so
            // it must never add latency to "the preview appeared." One
            // lookup per capture start, not per tick — the caret doesn't
            // move while Joe holds the key and talks, so re-querying every
            // tick would just be wasted AX round-trips. The generation check
            // on the way back guards against a slow lookup landing after
            // this capture already ended (stopPreview bumps previewGeneration
            // and hides the panel).
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                let caretRect = CaretLocator.caretScreenRect()
                DispatchQueue.main.async {
                    guard let self, generation == self.previewGeneration else { return }
                    self.previewOverlay.show(initial: "listening…", near: caretRect)
                }
            }
        default:
            break
        }

        let timer = Timer(timeInterval: Double(config.previewIntervalMs) / 1000.0, repeats: true) { [weak self] _ in
            self?.previewTick(generation: generation)
        }
        previewTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func previewTick(generation: Int) {
        guard generation == previewGeneration, let previewServer else { return }
        guard let wav = recorder.snapshotWav(lastSeconds: Double(config.previewWindowSeconds)) else { return }
        previewServer.transcribe(wavData: wav) { [weak self] text in
            guard let self else { return }
            DispatchQueue.main.async {
                // Drop late/out-of-order responses: only the request tagged
                // with the CURRENT generation is allowed to touch the field
                // (inline) or the overlay (panel). This is also what stops a
                // stale tick from typing after finalize() already ran —
                // gestureCaptureEnded bumps the generation (via stopPreview)
                // before the final transcription pipeline even starts.
                guard generation == self.previewGeneration, let text, !text.isEmpty else { return }
                switch self.config.previewStyle {
                case "inline":
                    // The server text isn't run through Transcriber.clean on
                    // the way out of PreviewServer (only whitespace-trimmed)
                    // — do it here so a hallucinated [BLANK_AUDIO]-style
                    // token never gets typed into Joe's field.
                    let cleaned = Transcriber.clean(text)
                    guard !cleaned.isEmpty else { return }
                    // "scratch that" (added 2026-08-08): interpret BEFORE
                    // apply() so saying it mid-utterance visibly backspaces
                    // what came before it, live, while the key is still held.
                    let interpreted = LiveTyper.interpret(cleaned)
                    self.liveTyper.apply(hypothesis: interpreted.kept)
                case "panel":
                    self.previewOverlay.update(text: text)
                default:
                    break
                }
            }
        }
    }

    private func stopPreview() {
        previewGeneration += 1 // invalidates any in-flight preview response
        previewTimer?.invalidate()
        previewTimer = nil
        previewOverlay.hide()
    }

    func gestureConversationToggled(on: Bool) {
        Log.shared.line("MODE conversation \(on ? "ON — hold space to talk" : "off")")
        cue(on ? config.soundModeOn : config.soundModeOff)
        settle()
        refreshMenuState()
    }

    private func settle() {
        setIcon(state: gestures?.conversationMode == true ? .conversation : .idle)
    }

    // MARK: - Status item

    private enum IconState { case waitingPermission, idle, recording, busy, conversation, error }

    private func setIcon(state: IconState) {
        let symbol: (name: String, description: String)
        switch state {
        case .waitingPermission: symbol = ("mic.badge.xmark", "quill-dictate: needs permission")
        case .idle: symbol = ("mic", "quill-dictate: idle")
        case .recording: symbol = ("mic.fill", "quill-dictate: recording")
        case .busy: symbol = ("waveform", "quill-dictate: transcribing")
        case .conversation: symbol = ("mic.square", "quill-dictate: conversation mode")
        case .error: symbol = ("exclamationmark.triangle", "quill-dictate: error")
        }
        DispatchQueue.main.async { [self] in
            let image = NSImage(systemSymbolName: symbol.name, accessibilityDescription: symbol.description)
            image?.isTemplate = true
            statusItem.button?.image = image
            statusItem.button?.toolTip = symbol.description
        }
    }

    private var conversationMenuItem: NSMenuItem!

    private func buildMenu() {
        let menu = NSMenu()
        let title = NSMenuItem(title: "Quill Dictate — hold right-⌘ to talk", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        conversationMenuItem = NSMenuItem(title: "Conversation mode (double-tap right-⌘)",
                                          action: #selector(toggleConversationFromMenu), keyEquivalent: "")
        conversationMenuItem.target = self
        menu.addItem(conversationMenuItem)
        menu.addItem(.separator())
        let reload = NSMenuItem(title: "Reload config", action: #selector(reloadConfig), keyEquivalent: "")
        reload.target = self
        menu.addItem(reload)
        let quit = NSMenuItem(title: "Quit Quill Dictate", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        menu.addItem(quit)
        statusItem.menu = menu
    }

    private func refreshMenuState() {
        DispatchQueue.main.async { [self] in
            conversationMenuItem.state = gestures?.conversationMode == true ? .on : .off
        }
    }

    @objc private func toggleConversationFromMenu() {
        // Same toggle the double-tap drives, reachable by mouse.
        gestures?.menuToggleConversation()
    }

    @objc private func reloadConfig() {
        config = Config.load()
        Log.shared.path = config.logPath
        Log.shared.line("INFO config reloaded")
    }

    private func cue(_ name: String) {
        guard config.sounds else { return }
        NSSound(named: name)?.play()
    }
}
