# Dictation rig: quill evaluation + build plan

*Decision-gate research pass per `CARR AI/00_Context/idea-inbox/2026-07-30-local-dictation-tool-note.md` (Addenda 1-5). Research and planning only — no installation, no downloads, no code in this pass. Written 2026-08-07.*

## Verdict

**ADOPT digimata/quill AS THE BASE for meeting mode.** It clears every Addendum 2 check: dual-stream mic+system capture, channel-separated by design, a documented integration hook, MIT license, trivial build. Meeting mode becomes an integration (wire our whisper.cpp + dictionary onto quill's capture and hook) rather than a build-from-scratch. Dictation mode (Addendum 3's second half) still gets built as specced — quill doesn't do push-to-talk desk dictation, and neither VoiceInk nor Handy beats a small purpose-built wrapper once quill supplies the shared engine plumbing.

## 1. Finding the real repo

Two different GitHub projects are named "quill." Only one matches the spec's description ("start recording, stop, entire audio transcribes into a default folder").

| | **digimata/quill** | woosublee/quill |
|---|---|---|
| Description | "Ultra-minimalist macOS recording + transcription" | Push-to-talk dictation app with Claude Code MCP integration |
| Stars | 3,727 (`curl api.github.com/repos/digimata/quill`, checked 2026-08-07) | 5 |
| Shape | Meeting recorder: click to start/stop, dual-track capture, auto-transcribe | Wispr-shaped: hold a hotkey, speak, paste at cursor |
| Fit vs. spec | Matches exactly — this is the @dremnik repo | Wrong tool class; would compete with our OWN dictation-mode build, not meeting mode |

**Verdict on identity: digimata/quill is the repo Addendum 2 means.** woosublee/quill is closer to a dictation-mode reference (noted in §3) but is not the "Granola alternative" described. All findings below are digimata/quill, license MIT, pushed 2026-07-30, default branch `master`.

## 2. Addendum 2 checks, with file-level evidence

Evidence source: `README.md` (raw, fetched 2026-08-07) and repo tree at `Sources/quill/` — `Audio/MicRecorder.swift`, `Audio/SystemAudioRecorder.swift`, `Transcription/{ParakeetEngine,TranscriptionCoordinator,TranscriptionEngine}.swift`, `RecordingSession.swift`, `Config.swift`, `Doctor.swift`, `Install.swift`, `Notify.swift`, `UI/MenuBarController.swift`.

**(a) System audio + mic, or mic only?** Both. One menu-bar click records "your mic and all system audio as two separate tracks" via `AudioHardwareCreateProcessTap` (Core Audio process tap, macOS 14.2+ API, no virtual device, no kernel extension) for system audio and `AVAudioEngine` for the mic. This is the *other side of the call*, captured without a loopback driver — better than the BlackHole path Addendum 3 assumed we'd need to build.

**(b) Separate streams or mixed?** Separate, by design, and this is the headline reason to adopt. Each session directory (`~/Recordings/<yyyy.MM.dd-HHmm>/`) holds `mic.caf` and `system.caf` as independent files. README: "Two tracks on purpose: speech models do better on clean single-source audio, and mic-vs-system is free two-party diarization — `me` vs `them` with no speaker-identification model." Tracks are transcribed separately, shifted to a shared clock by per-track offsets in `meta.json`, then merged by timestamp. This is exactly the channel-separated capture Addendum 2 asked for, and it ships today.

**(c) Can transcription point at our whisper.cpp + custom dictionary, or the output folder feed the ingest sweep?** Yes, via the second path, and it's clean. Quill's *built-in* engine is Parakeet TDT 0.6B v2 via Core ML (FluidAudio), not whisper — a whisper engine (WhisperKit, not whisper.cpp specifically) is listed as "planned," so there is no drop-in whisper.cpp adapter today. But `TranscriptionEngine.swift` defines a small protocol quill's own engine sits behind, and `config.json` exposes `"transcription": {"enabled": true|false, "engine": "parakeet"}` plus an `on_stop` hook: "shell command spawned with the session directory as its argument, after the transcript is written... Wire it to whatever comes next: summarization, filing, indexing." The integration is: set `transcription.enabled: false`, point `on_stop` at our own script, and run `mic.caf`/`system.caf` through `whisper-cli` with the CARR custom-vocabulary prompt ourselves. We never touch quill's Swift source. Raw dual-track audio always lands in `~/Recordings/<session>/` regardless of the transcription setting, so the ingest sweep can also just watch that folder directly.

**(d) License, language/framework, build complexity on Apple Silicon.** MIT (github.com/digimata/quill/blob/master/LICENSE, confirmed by API). Swift, single Swift Package Manager executable target, no Xcode project, no app bundle — "same skeleton" as the author's sibling tool `parrot`. Build is one command: `swift build -c release`, then `cp .build/release/quill /usr/local/bin/quill`. Requires macOS 15+ for the Core Audio tap API; this Mac runs macOS 26.5.2 (`sw_vers`, checked 2026-08-07), well clear. Build complexity: low — no Metal shader compilation, no model bundling at build time (models download on first transcription run if the built-in engine is used at all).

## 3. Alternates

**VoiceInk** (`Beingpax/VoiceInk`, GitHub, 5,791 stars, GPL v3, Swift, checked 2026-08-07): a menu-bar *dictation* app, not a meeting recorder — hotkey-triggered, transcribes via whisper.cpp locally (a real whisper.cpp integration exists today, unlike quill), has a "Power Mode" for per-app settings and a personal-dictionary feature that maps closely to our custom-vocabulary need. It does not do system-audio capture at all, so it cannot serve as the meeting-mode base — Addendum 2's core requirement is out of scope for it by design. It is however the single best *dictation-mode* reference: its whisper.cpp wiring and personal-dictionary implementation are worth reading before writing our own hotkey wrapper. GPL v3 (vs. quill's and Handy's MIT) means fork-and-modify is fine but redistributing a derivative has copyleft obligations we'd need to mind if we ever shipped code externally, which we don't.

**Handy** (`cjpais/handy`, GitHub, 28,959 stars, MIT, Rust/Tauri, checked 2026-08-07): the most popular tool in this class by a wide margin, also a push-to-talk dictation app (mic-only, no system-audio capture), built on Tauri/Rust rather than native Swift/AVFoundation. It bundles a model picker (Whisper Small/Medium/Turbo/Large, Parakeet V2/V3, Moonshine) and accepts custom GGML models directly, which is attractive, but the Rust/Tauri stack is a heavier, less native fit for a small macOS menu-bar utility than quill's or VoiceInk's plain Swift, and like VoiceInk it has no meeting-capture path. Reference-only, same as VoiceInk, and slightly lower priority than VoiceInk for the dictation-mode read since its custom-model support is the one feature edge it has over VoiceInk for our purposes.

**Neither VoiceInk nor Handy beats quill as the meeting-mode base** — neither captures system audio at all, which is Addendum 2's threshold requirement. Quill is uniquely positioned because it is the only one of the three actually shaped like Granola (session-based recorder) rather than shaped like Wispr Flow (hotkey dictation).

## 4. Local infrastructure already in place

- **whisper.cpp binary:** `/opt/homebrew/bin/whisper-cli` (Homebrew `whisper-cpp` 1.9.1, MIT-licensed port; confirmed via `brew info whisper-cpp` and direct invocation, 2026-08-07). Metal backend active, running on this Mac's Apple M1 Pro GPU (`ggml_metal_device_init` log on invocation).
- **Model on disk:** `~/.cache/whisper-cpp/models/ggml-small.en.bin` (487 MB, `small.en`).
- **Gap vs. spec:** Addendum 3 calls for "large-v3-turbo class model for accuracy." What's actually installed is `small.en`, a materially smaller/faster/less accurate model. **Action item for the build session:** download `ggml-large-v3-turbo.bin` (or `ggml-large-v3-turbo-q5_0.bin` for a smaller footprint) from the whisper.cpp model repo into the same `~/.cache/whisper-cpp/models/` directory before wiring meeting mode's transcription step to it. This is a download, so it needs an explicit go-ahead in the build session per the standing action-category rules — flagging here so it isn't a build-session surprise.
- **Custom vocabulary prompt:** not yet written anywhere found in `~/carr-system` or `~/.cache`. whisper.cpp's `--prompt` flag accepts the vocabulary seed described in Addendum 3 (GCCMLS, Sunbiz, CoStar, Tuerk Schlesinger, etc.) — this is new, to be authored during the build, not something to search for further.

## 5. Repo citizenship

Code lives in `~/carr-system`, no second home, per the standing rule. Proposed folder: **`~/carr-system/tools/dictation-rig/`** — new top-level tool directory (parallel to `fill-engine/`, `generators/`), holding the quill vendor build config, the `on_stop` transcription hook script, the custom-vocabulary prompt file, and (in the dictation-mode phase) the hotkey wrapper. Quill itself is a git submodule or a pinned-commit vendor checkout under `tools/dictation-rig/vendor/quill/`, not copied/forked source, so upstream fixes stay pullable.

**Repo state note:** the working tree is currently on branch `renewal-radar-promotion-loop-204` with one modified file (`hooks/guard-unattended.py`, uncommitted) and an untracked `.claude/`. The build session should start this work from a clean branch off `master`/main, not stack it on this unrelated in-flight branch.

## 6. Build plan — numbered work order

### Phase A: Meeting mode (per Addendum 3's build-order ruling)

1. **Vendor quill.** Add `tools/dictation-rig/vendor/quill/` pinned to the commit evaluated here (pushed 2026-07-30). Build with `swift build -c release`; confirm the binary runs (`quill doctor`) and both permissions prompts (mic, Screen & System Audio Recording) resolve cleanly on this Mac.
2. **Consent layer first, before any capture runs live** (Addendum 5, non-negotiable). Build the "Now Recording" audible announcement, played into the loopback path so the remote side hears it and the local speaker plays it in person. Implement as a short audio clip triggered at recording start, wired ahead of quill's own start action (menu-bar click intercepted by our wrapper, not quill's raw hotkey). Add the pre-flight "consent obtained?" checklist prompt from Addendum 2/5 as a blocking step before the record action fires. Log the announcement firing (who/when) into a header we prepend to the eventual merged transcript.
3. **Model upgrade.** Download `ggml-large-v3-turbo` (or the q5_0 quantized variant) into `~/.cache/whisper-cpp/models/`, after explicit go-ahead (this is a download, gated per standing rules). Smoke-test `whisper-cli` against a short sample `.caf` for latency and accuracy versus `small.en`.
4. **Custom vocabulary prompt.** Author the CARR dictionary prompt file (`tools/dictation-rig/vocab-prompt.txt`) seeded per Addendum 3 — GCCMLS, Sunbiz, CoStar, Tuerk Schlesinger, Miramar, Cadence Studio, active prospect surnames pulled from the record layer. Treat it as a living file the ingest sweep or a periodic job can refresh from current prospect names, not a one-time hand-type.
5. **Transcription hook.** Set quill's `config.json` to `"transcription": {"enabled": false}` (skip Parakeet entirely — we don't need it) and point `on_stop` at `tools/dictation-rig/bin/transcribe-session.sh <session_dir>`. That script runs `whisper-cli` twice (once per `.caf`, using the vocab prompt), shifts each transcript by its `meta.json` offset, and merges by timestamp into a labeled `me`/`them` transcript — reusing quill's own merge logic as the reference, reimplemented against whisper.cpp's JSON output shape (quill's merge code assumes Parakeet's output shape, not whisper.cpp's, so this step is a genuine build, not a passthrough).
6. **Jot-blend.** Build the fragment-merge step from Addendum 3: pull timestamped scratch notes from Joe's designated capture surface (phone quick-notes / scratch file) that fall within the session's time window, and thread them into the transcript-to-summary pass so the summary organizes around what Joe flagged, evidenced from the transcript.
7. **Ingest-socket landing.** Wire the finished `transcript.json`/summary into the existing ingest sweep (same landing pattern as the Notes-sweep call-recording path) so activities and follow-ups draft automatically, one-tap confirm, matching the SOP the corporate Teams/Copilot path already uses.
8. **Calendar-linking.** Auto-attach each session to its calendar event and resolved deal, using the same event-matching logic the pre-brief job already has.
9. **Diarization upgrade (optional, Addendum 5 §2).** If two-party `mic`/`system` labeling isn't enough for group calls, add local per-meeting diarization for Speaker N labels — Joe/Dell voices enrolled and self-consented only, explicitly no persistent third-party voiceprints, structural not policy.
10. **Smoke test end-to-end.** One real (consented) meeting, start to ingest-socket landing, before calling meeting mode done.

### Phase B: Dictation mode (desk, second per Addendum 3)

11. **Reference read.** Read VoiceInk's whisper.cpp wiring and personal-dictionary handling (§3 above) and, more lightly, Handy's custom-GGML-model handling, before writing the wrapper — both are read-for-approach only, never dependencies (Addendum 3 §6).
12. **Hotkey capture.** Build the push-to-talk wrapper (Hammerspoon or small Swift, per the original spec's choice) — hold hotkey, record mic via the same `AVAudioEngine` pattern quill already demonstrates, release to stop.
13. **Transcribe + clean.** Run through the same `whisper-cli` + vocab-prompt pipeline built in Phase A step 5 (shared engine, shared dictionary — this is why meeting mode went first). Strip fillers, apply whisper's own punctuation; optional Claude tone pass only when explicitly invoked.
14. **Paste at cursor.** Deliver text into whatever app is frontmost.
15. **Repo citizenship close-out.** Manifest entry, smoke test, docs — same bar as any other durable `~/carr-system` tool.

## Sources

- github.com/digimata/quill (README.md, master branch, fetched 2026-08-07; api.github.com/repos/digimata/quill, fetched 2026-08-07)
- github.com/woosublee/quill (README, fetched 2026-08-07, for repo-identity elimination only)
- github.com/Beingpax/VoiceInk (api.github.com/repos/Beingpax/VoiceInk license file, fetched 2026-08-07)
- github.com/cjpais/handy (api.github.com/repos/cjpais/handy, fetched 2026-08-07)
- Local: `brew info whisper-cpp`, `whisper-cli` invocation log, `ls ~/.cache/whisper-cpp/models/`, `sw_vers`, `git status`/`git branch` in `~/carr-system` (all run 2026-08-07)
