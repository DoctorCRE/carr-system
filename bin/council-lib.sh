#!/bin/zsh
# council-lib.sh — the ONE home for outside-model pins and invocations.
#
# Sourced by bin/council.sh (the panel) and bin/precheck.sh (the single-seat
# reviewer). Two copies of a model id and an effort flag is exactly the drift
# that let a council run at DEFAULT effort on 2026-08-09 while everyone assumed
# otherwise. Rule a8c55a47: a manual path and an automated path that do the same
# job must be the same code. Change a pin HERE and nowhere else.
#
# ── WHAT "VERIFIED" CAN AND CANNOT MEAN FOR THESE PINS ───────────────────────
# ASKING A MODEL ITS OWN NAME OR EFFORT IS CONFABULATION, NOT TELEMETRY.
# Measured live 2026-08-09, one sitting, this machine:
#   - Codex invoked with model=gpt-5.6-sol (the Sol id then; today's is
#     gpt-6.1-sol), effort=high self-reported
#     "GPT-5.4 effort=xhigh". Neither value was sent; xhigh cannot even be sent.
#     An earlier probe self-reported "GPT-5.4" while running on the Sol config,
#     and that wrong answer was briefly reported to Joe as fact.
#   - Grok invoked with --reasoning-effort low, then high, returned "effort=low"
#     BOTH TIMES — byte-identical answers for opposite flags. A latency probe
#     showed 83s vs 91s on one sample, inside noise.
# So: the flags are VERIFIED ACCEPTED (a wrong value like `expert` hard-errors)
# and the ids are VERIFIED REAL (`grok models`, ~/.codex/config.toml). Whether
# the remote side HONOURS the effort is NOT verified, and nothing here claims it
# is. Manifests say "requested", never "used".
#
# ── BILLING, checked 2026-08-09 ──────────────────────────────────────────────
# Both CLIs run on SUBSCRIPTION auth, not metered API keys. ~/.codex/auth.json
# is auth_mode=chatgpt with OPENAI_API_KEY null; grok reports "logged in with
# grok.com". No OPENAI_API_KEY or XAI_API_KEY in the environment. If an API
# charge ever appears, something changed and it should be investigated, not
# assumed normal.

# ── COUNCIL TIER — a panel reasoning about an open design question ───────────
# Joe, 2026-08-09: council meetings are always held with the Sol model on 'high'
# effort and SuperGrok Expert. Joe, 2026-09-29: any Sol model call uses
# gpt-6.1-sol (never gpt-6-sol or gpt-5.6-sol).
#
# CODEX / "Sol" -> gpt-6.1-sol. Bare `gpt-6.1` or `sol-6.1` style names are not
# valid ids; use the full id. Flags passed explicitly even though ~/.codex/config.toml sets
# them, because that file is machine-local and untracked — Dell's machine has
# none, and a council that silently downgrades elsewhere is the failure this
# file exists to prevent.
CODEX_MODEL="gpt-6.1-sol"
CODEX_EFFORT="high"

# GROK / "SuperGrok" -> grok-4.7 at high. Joe ruled 2026-09-29 that Grok calls
# use grok-4.7; Grok CLI 1.0.44 lists grok-4.7 as its only model (4.6 is gone).
# IMPORTANT: the CLI accepts ONLY high | medium | low; `expert` is rejected
# outright. "SuperGrok Expert" is the grok.com ACCOUNT TIER that entitles the
# session, not a per-call setting. So `high` is the top of the CLI's scale and
# is what Joe's instruction resolves to. If xAI ever exposes an expert effort
# or a newer model id, change it here.
GROK_MODEL="grok-4.7"
GROK_EFFORT="high"

# ── PRECHECK TIER — deliberately NOT the council tier ────────────────────────
# Joe, 2026-08-09: "i don't think sol 5.6 at high effort is necessary for all
# tasks, only for council stuff. its the equivalent of fable 5 and youre wasting
# it on something that could be handled by a lower model."
#
# He is right, and the measurement is brutal: the first precheck run — ONE
# one-page LOI, asked to find defects and judge a single classification — ran
# 33 MINUTES 57 SECONDS at council tier and was killed without returning. That
# is document triage against an explicit rubric, not open-ended reasoning. A
# pre-clear step meant to SHRINK Joe's queue cannot take half an hour; at that
# speed it makes the queue worse.
#
# Mirrors rule fb110a39 (Fable reserved for its best use cases) and the cost-tier
# half of 185013c6 (outside models are a delegation pool; tier is chosen per
# job). Model is the same gpt-6.1-sol as the council tier (the one Sol id Joe
# allows); EFFORT is the dial.
PRECHECK_MODEL="gpt-6.1-sol"
PRECHECK_EFFORT="low"


# STDIN MUST BE CLOSED, and this cost 45 minutes to find. `codex exec` reads
# stdin IN ADDITION to its prompt argument. Launched from a script whose stdin
# is an unclosed pipe, it prints "Reading additional input from stdin..." to
# stderr and blocks FOREVER. Two precheck runs were killed at 33m57s and 10m27s
# and diagnosed as "the model is slow at high effort" — it had not started
# reasoning at all, and out.md was 0 bytes the whole time. `< /dev/null` on
# every invocation. A hang that looks like latency is the worst kind, because
# the obvious fix (lower the tier) changes nothing and appears to be worth
# trying.

# run_codex <brief> <out>          — council tier
run_codex() {
  # --dangerously-bypass-hook-trust: the hooks in ~/.codex/hooks.json are OUR
  # OWN gate code (ops/config/codex-hooks.json is the tracked contract), not a
  # third party's. Codex requires persisted hook trust granted interactively
  # before it will run a hook at all, and an unattended `codex exec` never
  # gets that prompt — so without this flag every PreToolUse hook (including
  # guard-unattended.py) is SILENTLY SKIPPED, no denial, no warning. This flag
  # runs already-enabled hooks without requiring that interactive trust — it
  # is what turns enforcement ON for this unattended path. Verified live by a
  # negative smoke 2026-08-14: the identical probe ran clean without the flag
  # and was correctly blocked with it. See ops/codex-hook-smoke.sh.
  codex exec --sandbox read-only --skip-git-repo-check \
    --dangerously-bypass-hook-trust \
    -c model="\"${CODEX_MODEL}\"" \
    -c model_reasoning_effort="\"${CODEX_EFFORT}\"" \
    "$(cat "$1")" < /dev/null > "$2" 2>"${2:r}.err"
}

# run_grok <brief> <out>           — council tier
# Goes through the sanctioned runner (bin/grok-run.sh), never `grok` directly:
# it pins the model, returns the final assistant message, and verifies the end
# event (stopReason and modelUsage). A cancelled or wrong-model run exits
# nonzero, and its text is moved to <out>.rejected so it can never be read as
# a chair's answer. The runner's receipt lands in the .err file.
run_grok() {
  [ "$GROK_MODEL" = "grok-4.7" ] || { print -r -- "run_grok: runner is pinned to grok-4.7, GROK_MODEL=$GROK_MODEL" > "${2:r}.err"; return 4; }
  "${GROK_RUN:-$REPO/bin/grok-run.sh}" --effort "$GROK_EFFORT" --prompt-file "$1" \
    < /dev/null > "$2" 2>"${2:r}.err"
  local rc=$?
  [ $rc -eq 0 ] || { [ -e "$2" ] && mv -f "$2" "${2:r}.rejected"; }
  return $rc
}

# run_precheck <brief> <out>       — triage tier, NOT the council tier
run_precheck() {
  # --dangerously-bypass-hook-trust: same reasoning as run_codex above — these
  # are our own gate hooks, and without persisted interactive trust codex
  # silently skips them on an unattended run. This flag is what turns
  # enforcement ON here too (verified by the same 2026-08-14 negative smoke).
  codex exec --sandbox read-only --skip-git-repo-check \
    --dangerously-bypass-hook-trust \
    -c model="\"${PRECHECK_MODEL}\"" \
    -c model_reasoning_effort="\"${PRECHECK_EFFORT}\"" \
    "$(cat "$1")" < /dev/null > "$2" 2>"${2:r}.err"
}
