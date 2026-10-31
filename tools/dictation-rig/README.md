# dictation-rig — local meeting capture + transcription on our own whisper

The CARR dictation rig. Phase A: meeting mode. Phase B: desk dictation
(quill-dictate). Spec and rulings:
`CARR AI/00_Context/idea-inbox/2026-07-30-local-dictation-tool-note.md` (five addenda);
build plan: `specs/dictation-rig-build-plan-2026-08-07.md`; Phase B ruled design:
loop #243 / decision f799fd49.

## Phone session status

The Studio posts `state`, `reason`, `open_questions`, and `review_questions` to
`POST /capture/phone/v1/call-sessions/<id>/status` with the existing Studio bearer.
Each review question contains only `id` and `question`. This exchange sends no
transcript or narrative summary. The existing processor repeats the exchange
while questions remain open, and deduplicates unchanged status when none remain.

`GET /api/v1/call-sessions/<id>` and its `/report` read return `reason` (a code or
`null`), `open_questions` (a nonnegative integer), and `review_questions` (an array
of unanswered `{id, question}` objects). Only the owning partner can read these.
Active Studio states
`needs_attention` and `held_review` take precedence over the normal derived state.
Reasons are `extraction_failed`, `transient_ceiling`, `summary_repeats_transcript`,
`transcript_unavailable`, or `open_review_questions`. Open review questions alone
produce `held_review` with `open_review_questions`. A status update with
`{"state":null,"reason":null,"open_questions":0,"review_questions":[]}` clears the override.

The owning partner answers with `POST /api/v1/call-sessions/<id>/answers` and
`{idempotency_key, session_id, question_id, answer}`. `answer` is a nonempty string
of at most 1000 characters, including `confirm`, `dismiss`, or a written answer.
A successful response is `{ok:true, session_id, question_id, answer}`. Replaying
the same key and payload returns its receipt, including a later pickup outcome. Reusing the key with a
different payload or answering a resolved question with a new key returns 409.
An unknown question or another partner's session returns 404.

Answers remove questions from the partner read and reduce its count immediately.
The status response returns `question_answers` as `{question_id, answer}` objects.
The Studio applies them through local `post_call.resolve_question` and reports
`answer_outcomes` as `{question_id, outcome}` on the next status exchange.
The outcome is `applied`, or `already_resolved` when Call Mode resolved it first.
Session reads expose `answer_outcomes` for `already_resolved` answers so the app
can explain the result. Acknowledged answers stop appearing in `question_answers`.
The existing local status receipt carries outcomes until the exchange succeeds.
Studio reports are authoritative for open questions. Omitting `review_questions`
clears old unanswered questions while preserving answer receipts. Answer receipts stay in the existing
`studio_status` JSON so a delayed report cannot reopen an answered question.
The Studio hold clears after local pickup and the cleared status report.

## Two modes, one hard boundary

- **Meeting mode** (quill, Phase A): a counterparty exists, so the consent
  announcement is structural and non-negotiable.
- **Desk dictation** (quill-dictate, Phase B): Joe speaking to his own machine,
  no counterparty, so there is NO consent announcement — and the two modes stay
  visibly distinct (separate binaries, separate menu-bar icons, separate
  launchd labels) precisely so that boundary stays true. The privacy property
  of dictation mode is structural too: the mic is live only while a key is
  physically held.

Both modes share ONE transcription engine: `/opt/homebrew/bin/whisper-cli`,
ggml-large-v3-turbo, `vocab-prompt.txt`. Never a second engine.

Meeting transcription records the selected local model path and `model_sha256`
in the private `<session>/transcript.json` and logs that identity in `transcribe.log`.
The hash is computed once per session before inference, including fallback and
custom models. Set `CARR_WHISPER_MODEL_SHA256` in the transcription process
environment to require that exact 64-character SHA-256 before inference. A
malformed pin or mismatch fails the session before any audio is transcribed.
With no pin, existing model selection remains unchanged. The digest identifies
the bytes observed before inference and does not prevent later file replacement.

## Deal Room Call Mode

The Deal Room is a second control surface for meeting mode. The existing Quill
menu-bar icon remains available. The Deal Room's **Call Mode** button talks to a
loopback-only companion at `http://127.0.0.1:4682`; the companion clicks Quill's
existing Start/Stop menu item and reports the real recorder state back to the
page. It does not contain another recorder.

Install or refresh the companion with `bin/install-call-mode.sh`. The installer
loads `com.carr.call-mode` as a user LaunchAgent and verifies its state endpoint.
The Deal Room is opened at `app.doctorcre.com` (and still answers at
`dealroom.doctorcre.com`); the companion admits exactly those two web origins.
The first connection from either may trigger Chrome's Local Network Access
prompt. Allowing it gives that Deal Room origin access to the loopback
companion; every other web origin is refused by the companion.

**Accessibility permission, one-time, and it must go on the REAL interpreter.**
Call Mode clicks Quill's Start/Stop menu item via `osascript`/System Events, so
the agent needs Accessibility access. A LaunchAgent loaded from
`~/Library/LaunchAgents` gets its Accessibility responsibility attributed to
`ProgramArguments[0]`, and that cannot be `/usr/bin/python3`: that path is the
Command Line Tools shim, and macOS will not let a human usefully grant it
Accessibility (the click fails with `osascript is not allowed assistive access
(-25211)` even with python3, Python.app and osascript all enabled). The tracked
plist therefore names the real interpreter directly,
`/Library/Developer/CommandLineTools/usr/bin/python3` (verified live on the Mac
Studio 2026-09-23). Grant **that** path Accessibility under System Settings >
Privacy & Security > Accessibility. A machine without the Command Line Tools
fails visibly at load rather than silently.

The companion and transcription imports use only the standard library.
Post-call extraction runs separately with the repository's `.venv/bin/python3`,
which supplies `typesafe_sdk`. That worker loads only `TYPESAFE_API_KEY` from
`~/.config/carr/typesafe.env` through the named-key credential loader.
Keep the venv provisioned on each partner's Mac; do not install the SDK into
the Accessibility-granted interpreter.

The post-call model step runs `claude -p --model sonnet` headlessly through
the existing Claude subscription login. It sends the supplied transcript and
exact call context on stdin, disables tools and session persistence, and
validates the structured JSON before writing a review pack. The Claude CLI
must be installed and signed in on each partner's Mac. Model failures leave
the session blocked for retry, with its transcript and context retained.

**Risk color: red, human initiated.** Starting a call fires the audible consent
announcement and begins client-visible recording, so it only happens after Joe
or Dell clicks Start. No schedule or background trigger can start a recording.
Stopping starts the same local transcription hook used by the menu-bar path.

For the weekly Joe + Dell deal call, Call Mode writes `call-context.json` into
the new Quill session folder. Separate capture tracks then label the local mic
as Joe or Dell from that Mac's provisioned device identity, and label the system
audio as the other partner. Other calls use "Other participant" for system
audio. This is channel attribution, not biometric speaker recognition, and no
third-party voiceprint is created or retained.

The post-call extraction/report/draft layer is specified in
`specs/post-call-workflow-2026-08-10.md`. When a weekly transcript is ready,
the post-call worker fetches missing context through the existing local verb
route. It reads active deal IDs from `deal-room-board`, then their exact index
from `get-call-context`. A failed fetch leaves a reason in the session's post-call result (written by `bin/post_call.py`), and
the existing capture-poll sweep retries it. Deal Room can still supply context
through `POST /api/call-context`; the first stored index wins and processing
runs once. Confident next-step and phase changes follow the existing automatic
apply checks. Other proposals require review, and Outlook emails remain drafts.

## Phase B — quill-dictate (system-wide dictation to the active text box)

`dictate/` — own Swift package (menu-bar agent, no dependency on vendor/quill).
One gesture set, both keyboards (decision f799fd49; trigger emissions
live-corrected 2026-08-07):

- **HOLD the trigger key** = push-to-talk: speak while held, release, text
  lands at the cursor of whatever app has focus. The trigger is **right-cmd
  (54)** on both keyboards. On a Logitech keyboard whose receiver
  (046d:c52b) reports its immediate-right Command as HID Right Control and
  its farther-right Control as HID Right GUI, `bin/logitech-keymap.sh` +
  launchd `com.carr.logitech-keymap` swap those two usages for that
  vendor and product only, so the physical Command immediately right of space is
  Quill and the farther-right Control remains an ordinary Control key. The
  receiver's vendor and product ids cannot tell two different physical Logitech
  keyboards apart, so a keyboard that already reports those keys correctly
  (measured on Joe's Mac Studio, 2026-09-23) opts out per machine: if
  `$HOME/.config/carr/logitech-keymap.off` exists, the script clears any
  046d:c52b mapping instead of applying the swap. The MacBook keyboard is
  always untouched.
- **DOUBLE-TAP the trigger** = toggle conversation mode; inside it, **hold
  space** speaks, release disengages. A QUICK space tap types a normal space
  (replayed synthetically), so typing keeps working inside the mode; **Esc
  always exits it**. The menu-bar icon changes and an audible cue marks
  entry/exit. Space with modifiers passes through.
- Trigger flagsChanged events are CONSUMED — the agent owns those keys.
  Required because Siri's "press either cmd twice" shortcut kept firing on
  the double-tap regardless of the Settings dropdown (live, 2026-08-07).
  Trigger+key chords still reach apps (keyDowns carry their own flags), and
  any other key pressed during a hold aborts capture into an ordinary
  shortcut. Normal typing and left-hand shortcuts are untouched.

While a capture is held, the default **inline live preview** types words into
the focused field (resident whisper-server on small.en, ~1s cadence). The
final insert always comes from the full-accuracy large-v3-turbo pass. If Joe
keeps typing while that pass finishes, Quill preserves plain appended text
and replays it after the corrected dictation; mouse clicks, app changes,
shortcuts, and navigation keys still block the rewrite so a stale caret is
never trusted. A panel style remains available for apps that prefer it; tune
or disable preview via `preview_style`, `preview_interval_ms`, and
`preview_window_seconds` in config.

Trigger keys live in `~/.config/quill-dictate/config.json`
(`trigger_key_codes`, a list — one entry per keyboard; a future keyboard is a
one-line change).
Insertion is clipboard-paste (saved string restored 2s later; a non-string
clipboard — image, file — is not resurrected, known v1 limit) or `"type"` mode
for synthetic keystrokes. A silence gate (`min_peak_level`, min 0.35s) stops
whisper hallucinating text onto an empty press. Apple's built-in dictation was
verified never-enabled on this Mac (no `Dictation Enabled` key), so nothing
collides with the trigger.

Build: `bin/build-dictate.sh` (same toolchain guard as quill — see Build).
Deploy: `bin/install-dictate.sh` (config + launchd agent
`com.carr.quill-dictate`; `--config-only` skips launchd). Diagnostics:
`quill-dictate doctor` (headless health check) and
`quill-dictate transcribe <wav>` (engine test without keyboard or permissions).
Permissions, one-time: Accessibility (event tap + paste) and Microphone.
SIGNING (2026-08-08): the build signs the binary with a self-signed keychain
identity ("CARR Quill Dictate"), pinning the Designated Requirement to
`identifier "com.carr.quill-dictate" and certificate leaf = H"..."`. Neither
half changes when the code does, so the Accessibility grant now SURVIVES
rebuilds — before this, every rebuild produced a cdhash-based DR and cost a
remove-and-re-add in System Settings. A machine without that identity (Dell's,
a fresh checkout) still builds fine, just unsigned and back to re-granting per
rebuild; recreate it in Keychain Access > Certificate Assistant > Create a
Certificate, type Code Signing, named exactly "CARR Quill Dictate". Logs: `~/Library/Logs/quill-dictate.log`.

Resident-server reliability (repaired 2026-08-10): preview whisper, final
whisper, and correction cleanup send their chatty stdout/stderr to `/dev/null`,
never to an unread pipe (the old 16 KiB pipes filled and deadlocked all three
servers while their ports still looked live). Preview recycles its server after
three consecutive request failures. If the final server stops answering, Quill
stops it before the CLI fallback loads the same Metal model, then starts a clean
resident server after that utterance.
`quill-dictate doctor` probes HTTP responsiveness, not just open ports, so a
blocked-but-listening server now reports as failed.

## Shape (Phase A: meeting mode)

- `vendor/quill/` — digimata/quill, pinned submodule (commit 855869e, MIT, evaluated
  2026-08-07). Does the capture: one menu-bar click records mic + all system audio as
  two separate tracks (`mic.caf` = me, `system.caf` = them) into
  `~/Recordings/<yyyy.MM.dd-HHmm>/`. We never modify its source.
- `bin/transcribe-session.sh` — quill's `on_stop` hook. Runs our whisper-cli
  (large-v3-turbo) on both tracks with the CARR vocabulary prompt, shifts each track
  by its `meta.json` offset onto one clock, merges by timestamp into a labeled
  `me`/`them` transcript. Quill's own Parakeet engine is disabled in config; the raw
  audio and the transcript both stay on this Mac.
- `vocab-prompt.txt` — the whisper `--prompt` seed: CARR nouns, platforms, and
  market towns. PURE PROMPT TEXT, no comments — the whole file is fed to the
  model (~224-token budget). Tracked; this repo is public (WR-000049,
  2026-09-24), so it never carries a client roster.
- `vocab-local.txt` — gitignored, same directory: active client/prospect
  surnames, per-machine. Both `bin/transcribe_session.py`'s `load_prompt()`
  and `Transcriber.swift`'s `vocabPrompt` load it, if present, and append it
  to the tracked prompt, so dictation quality is unchanged; missing is not an
  error (a fresh clone has none yet). Refresh it from the live deal board when
  the active book changes (source: the `deal-board` verb); a periodic refresh
  job is a Phase B follow-up.
- Consent layer — see below. Non-negotiable tool behavior per Addendum 5.

## Build

`bin/build-quill.sh` — the only sanctioned build path. It carries the toolchain
fix (this Mac's CommandLineTools lacks its own libc++ headers; the script points
`CPLUS_INCLUDE_PATH` at the SDK's). Never run a bare `swift build` from docs.
Never run `swift package update` in vendor/quill without a fresh security review
— Package.resolved is the locked, reviewed dependency set (FluidAudio 0.15.5,
swift-argument-parser 1.8.2, scan of 2026-08-07).

## Config (quill side)

`~/.config/quill/config.json` is deployed by `bin/install-config.sh`:
transcription disabled (we own the engine), `on_stop` pointed at
`transcribe-session.sh`.

## The consent rule (Addendum 5, non-negotiable)

Meeting mode never runs silently. Recording start triggers an audible
"Now recording" announcement; the transcript header records that it fired and when.
The announcement plays on the local output device — in person everyone hears it; on
a call the remote side hears it when audio routes through speakers, and mic pickup
carries it into the recording either way, so the session itself carries proof of
disclosure. Known limit, stated honestly: with headphones on, the remote side does
not hear the local announcement — injecting audio into the outbound call path needs
a virtual-mic loopback device, which is a Phase B item. Until then the ask-first
habit governs headphone calls, exactly as Joe already does on the phone.

Florida is all-party consent. The tool enforces the floor; Joe's ask-first habit is
the craft on top.

## Known limits (Phase A, honest)

- Whisper hallucinates on near-silent tracks (the classic "Sous-titrage Société
  Radio-Canada" on an empty channel). A real call fills the system track so this
  mostly bites test recordings; a no-speech/energy gate on segments is a Phase B
  cleanup.
- The announcement plays on the local output device. With headphones, the remote
  side does not hear it — see the consent section. Ask-first governs there.
- Jot-blend, ingest-socket landing, and calendar-linking are build-plan steps 6-8,
  not yet built.

## No third-party voiceprints — structural

Two-party attribution comes free from the channel split (mic = me, system = them).
Within one channel, `bin/speaker_split.py` runs FluidAudio's on-device offline
diarizer (built by `bin/build-quill.sh` as `fluidaudiocli`) so several voices get
per-recording labels: "Other participant 1, 2…" on the system track, and on the
mic the voice with the most talk time keeps the channel label while the rest
become "In-room speaker 1, 2…". The labels mean nothing outside that recording.
The diarizer's output carries voice embeddings, so it is written to the temp
directory and deleted as soon as it is read. If the diarizer is missing or fails,
each channel keeps its single label, as before.
There is no speaker-enrollment feature for anyone but Joe/Dell (self-consented,
Phase B if built at all), and no persistent voiceprint of any client or third
party, ever.

## Dell setup (both modes, from his existing clone)

Dell already has `DoctorCRE/carr-system` cloned; the whole rollout is local.
In order, on his Mac:

1. `git pull` on main, then `git submodule update --init tools/dictation-rig/vendor/quill`
   (the submodule is a pointer; a plain pull does not fetch quill's source).
2. Pre-flight: `sw_vers` must show macOS 15+ (quill's system-audio tap API),
   and `brew install whisper-cpp` for `whisper-cli`.
3. Model: download `ggml-large-v3-turbo.bin` into `~/.cache/whisper-cpp/models/`
   (~1.6 GB, from the whisper.cpp models repo — same full-model choice Joe
   ruled; `ggml-small.en.bin` works as the fallback slot).
4. **Vocab prompt is per-partner**: `vocab-prompt.txt` (tracked, generic
   terms only) is shared as-is. Create his own `vocab-local.txt` (gitignored,
   same directory) from DELL'S live book (deal-board verb) — do not inherit
   Joe's client surnames; that file never syncs between machines.
5. Build both: `bin/build-quill.sh`, then `bin/build-dictate.sh` (each carries
   the CPLUS_INCLUDE_PATH toolchain guard — never bare `swift build`).
6. Deploy: `bin/install-config.sh` (meeting mode), `bin/install-consent.sh`
   (consent watcher), `bin/install-dictate.sh` (dictation agent + launchd).
7. His one-time permission clicks: mic + Screen & System Audio Recording
   (quill), mic + Accessibility (quill-dictate).
8. **Trigger keys are per-keyboard**: hold-to-talk defaults to right-cmd (54).
   Joe's Logitech-specific E4/E7 swap is scoped by receiver vendor and product;
   another keyboard that emits a different keycode can add it to
   `trigger_key_codes` in `~/.config/quill-dictate/config.json`. If his Mac
   binds Siri to a command double-press, the agent's event consumption already
   suppresses it.
9. Consent still ships: Alabama is one-party consent but Florida is
   all-party, the territory spans both, and the announcement layer plus the
   ask-first habit are non-negotiable either way (Addendum 5).

If a session is held for repeating the transcript, edit its private `post-call-held-draft.json` (or `post-call-report.json` for a saved report), then run `bin/call-mode.py release <session>`. The command checks the current transcript again and reports the failing field without printing its text.
