---
name: watch-video
description: >
  Lets Claude Code "watch" a video — a YouTube/Vimeo/Loom URL or a local file — by downloading it,
  extracting scene-change frames with ffmpeg, pulling captions, and analyzing frames + transcript
  together. Use when Joe wants a video summarized, mined for tactics, or checked for specific
  content: a CARR webinar, another agent's training or market-update video, a video post for the
  inspiration bank. Say "watch this video", "what does this video say about X", "pull the tactics
  from this webinar". LOCAL Claude Code only (needs ffmpeg + yt-dlp on the Mac) — not Cowork cloud.
  Do NOT use on meeting/call recordings: Teams meetings are Copilot's lane (division-of-labor rule
  `8aefcdce`, DNA/compiled-rules-shared.md), and client-call recordings carry consent/privacy weight — flag and stop if asked.
---

# Watch Video — vetted CARR adaptation (July 6, 2026)

> **Guidance changes:** Propose an edit to the relevant CLAUDE.md, AGENTS.md or skill in a PR, with the trade-off. Proposals bind nobody until merged; Git history preserves attribution.

Concept from github.com/bradautomates/claude-video, reimplemented as pure instructions so no
third-party scripts run on Joe's machine. Claude Code drives yt-dlp and ffmpeg directly.

## 0. Dependencies (first run only)
`which ffmpeg yt-dlp whisper-cli` — if missing: `brew install ffmpeg yt-dlp whisper-cpp` (ask Joe before installing).
- **Local transcription (set up July 7, 2026):** `whisper-cli` (whisper.cpp, Metal/GPU, fully offline) is installed, with the model at `~/.cache/whisper-cpp/models/ggml-small.en.bin` (~465 MB). This is the ON-DEVICE fallback when a video has NO captions — it does NOT count as an external transcription API, so it's fine to use without extra permission. (Bigger model = more accuracy if ever needed: `medium.en` from huggingface.co/ggerganov/whisper.cpp.)

## 1. Get the video + transcript
Work in a temp dir (e.g. `/tmp/watch-<slug>/`), never inside CARR AI or a synced folder.
- URL: `yt-dlp -f "bv*[height<=720]+ba/b[height<=720]" --write-auto-subs --write-subs --sub-langs "en" --convert-subs srt -o "video.%(ext)s" "<URL>"`
- Local file: skip download; check for a sidecar subtitle file.
- If captions came down, read the .srt — that's the transcript, free.
- **No captions → transcribe locally (don't stop at frames-only anymore).** Pull audio, downsample, run whisper:
  - `yt-dlp --referer "<page-referer>" -f "ba/b" -o "audio.%(ext)s" "<video-URL>"` (audio only — skip the video)
  - `ffmpeg -y -i audio.* -ar 16000 -ac 1 -c:a pcm_s16le audio.wav` (whisper needs 16 kHz mono)
  - `whisper-cli -m ~/.cache/whisper-cpp/models/ggml-small.en.bin -f audio.wav -osrt -otxt -of transcript -pp` → `transcript.srt` (timestamped, for citing moments) + `transcript.txt`. A ~60-min talk runs in several minutes on the GPU — launch it in the background.
  - Only if local transcription is unavailable (e.g. not on this Mac) do you fall back to frames-only, and say so. Never send audio to an EXTERNAL transcription service without Joe's explicit OK.

## 2. Extract scene-change frames (the part that beats fixed-interval sampling)
`ffmpeg -i video.* -vf "select='gt(scene,0.3)',showinfo,scale=512:-1" -vsync vfr frames/f%04d.jpg`
- Grep the showinfo output for `pts_time` to timestamp each frame.
- Budget by duration (`ffprobe -v error -show_entries format=duration -of csv=p=0 video.*`):
  ≤1 min ~40 frames · 1–3 min ~60 · 3–10 min ~80 · >10 min cap at 100 and WARN Joe the scan is
  sparse — offer to re-run a specific segment densely (`-ss <start> -to <end>` before `-i`).
- Too many frames? Raise the scene threshold (0.4, 0.5). Too few (static talking head)? Fall back to
  interval sampling: `-vf "fps=1/10,scale=512:-1"`. Use `scale=1024:-1` only when on-screen text matters.

## 3. Analyze
Read the frames (they're images — view them) alongside the timestamped transcript. Answer Joe's
actual question, citing timestamps. Mining for tactics/patterns → note each with its timestamp so
Joe can jump to it.

## 4. Route what was learned (this system's capture rules apply)
**The scope checkpoint applies here too (Joe's rule, July 11, 2026): once the transcript is done
and before anything is written anywhere permanent, summarize what the source contains and ask Joe
whether it applies system-wide or belongs somewhere more specific (own section/business model,
one prospect, personal tier, or nowhere).** Ask Joe whether findings belong anywhere permanent before writing: tactics worth repeating →
the `add-loop` verb with kind `idea` (`00_Context/idea-bank.md` is a GENERATED render of those rows — never edit it), or, when the tactic is a standing rule meant to bind future sessions, a proposed edit to the relevant guidance file or skill in a PR with Joe's verbatim words and the trade-off; proposals bind nobody until merged; post patterns →
content-inspiration-bank Section 1 with the transferable pattern named; market data points →
substance bank (dated, sourced). Then delete the temp dir.

**Learning-source captures — ONE process for every source (policy generalized July 8, 2026, Joe's
call): podcasts, YouTube channels, webinars, conference recordings, and member-gated CARR material
(training portal / Agent Central) are all the SAME pipeline — the portal is just one source among
many, and the system should never describe this as "pulling transcripts from the portal."**
The pipeline for ANY learning capture:
1. **Dedup first:** check `CARR AI/DNA/Marketing/Source Material/INDEX.md` (the capture log) — if the
   session is listed, it's already absorbed; stop. **Also check `CARR AI/DNA/Research/open-questions.md`
   (added Jul 31, 2026): if the source ANSWERS an open question there, close that row (dated, answer +
   source pointer) as part of the capture — answers must find their questions.**
2. **Transcription is a WORKING step only:** transcribe on-device → distill → **DISCARD the
   transcript with the temp dir.** No transcript file, no inline appendix, no exceptions for
   "re-mining" — re-watch/re-listen at the source instead. The source link is the record; do NOT
   archive source .pptx/PDF decks either (re-pull on demand).
3. **SCOPE CHECKPOINT — ask Joe BEFORE routing anything (Joe's rule, July 11, 2026).** Once the
   transcript (and any attached docs) are in hand and BEFORE distilling into or touching any
   permanent file, give Joe a short summary of what the source contains and ask how he wants it
   scoped: system-wide doctrine (merge into the shared DNA playbooks), something more specific
   (its own separate section/part or business model, a particular prospect or file, personal
   tier), or not captured at all. Never co-mingle transcript-derived info into existing doctrine
   without his answer. (Origin: the National Accounts session was auto-merged into territory
   pipeline doctrine as a sourcing channel; national accounts are a separate business model and
   had to be pulled back out into their own part.)
4. **Output is PARAPHRASE-ONLY:** no timestamps, no statements attributed to named colleagues —
   write the operational knowledge plainly as our own doctrine. Presenter + session identity go
   ONLY in the capture-log row; who-knows-what directory facts go to
   `CARR AI/DNA/Network/carr-colleague-contacts.md`. Exception: founder material from PUBLIC sources
   keeps its attribution (it feeds content deliberately).
5. **Knowledge MERGES at the scope Joe chose in step 3 — never create a per-session file.**
   Default domains: DNA/Deal Management/playbooks/{renewals,negotiation,financing,diligence-and-valuation}.md ·
   DNA/Leads/pipeline-craft.md · DNA/Network/vendor-relationship-craft.md · content lanes → the concept
   library/bank. Read the target domain file, integrate where it belongs, dedupe against what's
   already there, and log the capture-log row as 'merged into <file> §<section>'. If the
   device bridge is down, stage a digest in DNA/Research/capture-inbox/ for the next session to merge.

*Why member-gated CARR material gets extra weight: it's proprietary — that's what drove the
no-stored-transcripts rule. Public sources follow the identical process anyway, for consistency
and so the doctrine stays uniformly paraphrase-shaped.*

## Guardrails
- Public or Joe-owned videos only. No meeting/call recordings (Copilot's lane; consent/privacy).
- No patient-related video content of any kind (HIPAA).
- Nothing from the video publishes anywhere without going through write-content + Joe's review.

## Troubleshooting (from the first live run, July 6, 2026 — Joe's test worked)
- **Quote the sub-lang value** — use `--sub-langs "en"`, never an unquoted `en.*` (zsh expands the glob before yt-dlp sees it → "no matches found"). The skill's Step-1 command is already correct; this note is for improvised invocations.
- **"no impersonate target available" WARNING** — usually harmless; captions still download. If a video actually fails on it, that's YouTube bot-detection; try again or note it to Joe rather than fighting it.
- **Captions present → frames skipped is CORRECT** (talking-head/marketing videos are audio-only). Frames fire when there are no captions OR the visual carries the meaning (property tour, design, graphics). If Joe wants the visuals analyzed on a caption-having video, say so explicitly and force the frame path.
- **Login-gated / private-Vimeo pages (e.g. `training.carr.us` — learned July 7, 2026).** CARR's training portal embeds each session as a private, domain-restricted Vimeo in an iframe; a plain `yt-dlp <page-url>` fails ("Unsupported URL") and anonymous access 403s. What works: (1) find the Vimeo id from the iframe (`/video/<id>`); (2) `yt-dlp` the player URL WITH `--referer "https://training.carr.us/"` — Vimeo's domain-privacy only checks the referer, so this streams the audio/video fine (no login needed). Do NOT navigate the top browser frame directly to the player URL — that drops the referer and the config 403s. For attached files (slide decks under `wp-content/uploads/…` that curl can't reach because uploads are auth-walled), fetch them through Joe's authenticated Chrome session (`fetch(url,{credentials:'include'})` → blob → trigger a download), not curl. The **slide deck is often the better substance source than the narration** — grab both when a session has both.
