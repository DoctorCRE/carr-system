#!/bin/zsh
# The per-step wall-clock limit for bin/nightly.sh. Sourced, never executed.
#
# WHY THE CHAIN NEEDED THIS. step() has always survived a step that FAILS — it
# records the bad exit and keeps going, so a broken export cannot skip the
# backup. It did not survive a step that HANGS. On 2026-08-23 the 02:05 run
# stalled inside `exports (6 targets -> OneDrive)` on a half-closed Postgres
# socket (TCP CLOSE_WAIT, 0.30s of CPU across fourteen minutes) and never
# returned, so the encrypted backup — the last step — never ran either.
#
# The worse half is the singleton lock. A hung holder never releases it, so
# every later invocation takes the lock's no-op path and returns EXIT 0. A hung
# chain, a skipped chain and a clean chain all return the same number. Nothing
# in the system would have said a word; it was found by reading out/nightly.log.
#
# WHY THIS LIVES IN ITS OWN FILE. The selftest drives the same function the
# chain drives, rather than a copy of it that can drift away from the original
# while both keep passing.

# WHERE THE DEFAULT COMES FROM, measured rather than guessed. Across 149 step
# observations in out/nightly.log the slowest step that ever COMPLETED was the
# golden workflow suite at 327s; the next slowest was exports at 81s. 900s is
# roughly 2.75x the slowest completed step, which is wide enough that an
# ordinary slow night is never cut off and narrow enough that a wedged step
# cannot eat the chain.
#
# THE LIMIT IS NOT A PERFORMANCE TARGET. Hitting it means a step stopped making
# progress, not that it was slow. Do not tighten these to make the chain feel
# brisk: a false timeout throws away real work and writes a FAIL that has to be
# read in the morning.
: ${STEP_TIMEOUT_DEFAULT:=900}

# Per-step overrides, keyed by the leading words of the step label up to the
# first parenthesis — the same key record_run() derives, so a reworded
# parenthetical keeps its override. A value of 0 means no limit at all.
typeset -gA STEP_TIMEOUT_OVERRIDE
STEP_TIMEOUT_OVERRIDE=(
  # Its own record is 327s and it grows with the verb count, so it gets 5.5x
  # rather than the default's 2.75x.
  "golden workflow suite" 1800

  # THIS ONE IS NOT ABOUT SPEED, IT IS ABOUT A BUDGET DECLARED SOMEWHERE ELSE.
  # exporters/common.py sets PROVIDER_WAIT_BUDGET_SECONDS (default 1200, env
  # CARR_PROVIDER_WAIT_SECONDS) and waits that long for OneDrive to hydrate six
  # dehydrated placeholders before it exports anyway. That budget is LARGER than
  # the 900s default above, so the step could be killed 300s before its own
  # fail-open could ever be reached, and the export work had not started.
  #
  # It was. The 900s default was measured in #526 when exports completed in 81s,
  # with no warm-up stage in front of them; the warm-up arrived separately in
  # #1070 and nothing reconciled the two numbers. Every night from 2026-09-18
  # onward logged "1200s left before the exports start anyway" and then
  # "TIMEOUT after 900s ... exit 124" -- 09-18, 09-20 and 09-21 all identical.
  # The warm-up never once reached the point of giving up and working.
  #
  # 1800 is the warm-up budget plus 600s, which is 7.4x the 81s the export work
  # itself has ever needed. Raising CARR_PROVIDER_WAIT_SECONDS above 1200
  # WITHOUT raising this re-creates the bug, so keep this strictly greater than
  # that budget.
  "exports" 1800

  # ops/onedrive-prepublish-wake.py (defect: nightly.exports failed 9/22-9/24,
  # hanging the FULL 1800s on 9/24 with no partial output — see
  # out/nightly-runs/nightly-20260924T080221Z.log). This step's only job is a
  # single stat+small-read PER TARGET to nudge the File Provider awake before
  # the exports step's own wait_for_provider() begins; it has no internal
  # retry loop and no internal budget, so it is bounded ENTIRELY by this
  # external wall-clock. Short on purpose — six single-attempt probes, and a
  # provider that is genuinely dark should not cost the chain more than this
  # before the exports step's own, much larger, wait begins.
  "onedrive pre-publish wake" 120
)

# carr_step_timeout_for <label> — seconds this step is allowed.
carr_step_timeout_for() {
  local key="${1%% \(*}"
  print -r -- "${STEP_TIMEOUT_OVERRIDE[$key]:-$STEP_TIMEOUT_DEFAULT}"
}

# carr_step_timeout_prefix <seconds> — fills CARR_STEP_TIMEOUT_ARGV with the
# words to put in front of a command to put it under the wall clock.
#
# IT RETURNS A PREFIX RATHER THAN RUNNING THE COMMAND because of where it has to
# sit in the chain. step() runs its command through carr_routine_exec, which is
# a zsh FUNCTION ending in `env -i ... "$@"` — it strips the environment down to
# the declared database capabilities. A python wrapper cannot exec a zsh
# function, so the wall clock cannot go outside carr_routine_exec; it has to go
# inside it, which means step() needs words to insert rather than a call to
# make. The stripped environment is inherited straight through the wrapper, so
# the credential boundary is exactly where it was.
#
# FAILS OPEN. If the helper or its interpreter is missing the prefix comes back
# EMPTY and the command runs unwrapped rather than not at all. An unwatched step
# is last night's behaviour; a step that cannot start is worse than the bug being
# fixed. The same reasoning already governs md_renders_retired() in
# exporters/run_exports.py.
typeset -ga CARR_STEP_TIMEOUT_ARGV
carr_step_timeout_prefix() {
  local limit="$1"
  local root="${CARR_REPO_ROOT:-$REPO}"
  local helper="$root/bin/with-timeout.py"
  local py="$root/.venv/bin/python"
  CARR_STEP_TIMEOUT_ARGV=()
  [ -x "$py" ] || py="$(command -v python3 2>/dev/null)"
  [ -n "$py" ] && [ -f "$helper" ] || return 0
  CARR_STEP_TIMEOUT_ARGV=("$py" "$helper" "$limit")
}

# carr_step_with_timeout <seconds> <command...> — run a command under the wall
# clock directly. Used where there is no environment-stripping wrapper to sit
# inside of. It resolves the prefix through the SAME function step() uses, so
# there is one place that decides the interpreter, the helper path and the
# fail-open behaviour, and no second copy to drift away while both keep passing.
carr_step_with_timeout() {
  local limit="$1"; shift
  carr_step_timeout_prefix "$limit"
  "${CARR_STEP_TIMEOUT_ARGV[@]}" "$@"
}
