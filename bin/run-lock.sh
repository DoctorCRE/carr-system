#!/bin/zsh
# run-lock.sh — one unattended chain at a time. Sourced, not executed.
#
# WHY. On 2026-08-14 the nightly chain reported 30 tampered vault files and
# nothing had been tampered with: the scheduled 02:05 run and a manual run
# overlapped, and the second run's vault-drift check read the files the first
# had just re-exported. Every quarantined diff was a single line, the export
# timestamp. That is the cheap symptom. Two chains against one database also
# race on the export ledger, the encrypted backup, the drift baseline, and the
# consumer rebuilds — and the chain had no mutual exclusion of any kind, so
# running ./bin/nightly.sh while the scheduled one was mid-flight was always
# going to produce two writers and a false alarm.
#
# Manual and scheduled runs take the SAME lock through the SAME code, per rule
# a8c55a47: a manual path and an automated path that do the same job must be the
# same code, or they drift and only one of them is ever tested.
#
# Usage — the traps first, then the lock:
#   trap '<caller-exit-handler>; exit 143' INT TERM HUP   # the handler releases
#   trap '<caller-exit-handler>' EXIT
#   source "$(dirname "$0")/run-lock.sh"
#   carr_take_lock nightly || exit 0            # loser exits 0: a duplicate is a no-op
#
# THE TRAPS GO FIRST NOW (2026-08-23). They used to be installed immediately
# after a successful carr_take_lock, which is fine for releasing and useless for
# reporting: a chain that dies BEFORE it reaches the lock has no handler at all,
# and that is the shape of the 2026-08-17..19 outage, where an unset variable
# killed bin/nightly.sh above this point on three consecutive nights and left
# nothing in any log. The caller's handler now owns both jobs and guards the
# release on a flag it sets after the lock is actually taken.
#
# THE TRAPS BELONG AT THE CALLER'S TOP LEVEL, NOT IN THIS FUNCTION, and that is
# not a style preference. zsh runs an EXIT trap set inside a function when the
# FUNCTION returns, not when the shell exits — the first cut of this helper set
# its own trap, released the lock on the way out of carr_take_lock, and both
# racers in the test won. Measured, not reasoned about.
#
# CORRECTNESS DOES NOT DEPEND ON THE TRAPS FIRING, and it cannot: SIGKILL runs
# no trap at all, and a SIGTERM that arrives while a long step is in flight is
# not handled until that step returns — this chain's steps take minutes, so a
# machine going down can and will take the lock's holder with it while the lock
# still sits there. A hard kill, a panic, or a reboot mid-chain all leave the
# lock directory behind with nothing to release it.
# So the holder is identified by pid and checked for liveness: a lock whose pid
# is gone is reclaimed by the next run. Traps keep the common case tidy; pid
# liveness is what makes it safe to leave unattended. Without the reclaim, one
# crash would silently stop every nightly run from that day forward — quieter,
# and worse, than the failure this helper exists to prevent.
#
# ATOMICITY. mkdir is the atomic primitive, not a test-then-write: it either
# creates the directory or fails, in one syscall, on every filesystem this repo
# runs on. `[ -e lock ] || touch lock` has a window between the test and the
# touch that two chains firing on the same wake will eventually land in.
#
# Tested by tools/test-run-lock.py, written before this file (rule e65efc68) and
# run by ops/ci.sh's suite. That test also asserts bin/nightly.sh wires all four
# lines above, because a helper nobody wires is not mutual exclusion.

# ── A LIVE HOLDER IS NOT THE SAME AS A WORKING ONE (2026-08-23) ──────────────
# pid liveness answers "did the holder die and leave this behind". It cannot
# answer "is the holder still doing anything", and that is the case that cost
# real time: on 2026-08-23 a stalled nightly run held this lock while very much
# alive, so the next two launches read a live claim and exited 0 as well-behaved
# duplicates. The run that finally completed started 76 MINUTES LATE, and the
# only reason it ran at all is that somebody launched it a third time by hand.
#
# THE BOUND IS A CLOCK, NOT A HEARTBEAT, on purpose. A heartbeat file the holder
# touches per step would be tighter and would also be one more thing that can
# stop being written while the process lives — the same class of problem one
# level down. An age bound needs nothing from the holder, which is exactly the
# point: the holder is the component that has stopped being trustworthy.
#
# 5400s (90 minutes) is roughly SIXTEEN TIMES the chain's 5m39s healthy run and
# comfortably clear of its worst legitimate night: bin/step-timeout.zsh caps one
# step at 900s by default and the golden suite at 1800s, so even a night where
# several steps ride their wall clocks to the end lands well inside it. The bound
# exists to break a wedge, never to race a slow night — a false break throws away
# real work and starts a second writer against one database, which is the exact
# thing this file was written to prevent.
: ${CARR_LOCK_STALE_AFTER_SECONDS:=5400}

# How long a broken holder gets to honour TERM before it is KILLed. Ten seconds
# because this chain's holder is a zsh script that spends nearly all its time
# waiting on a child: zsh does not run its TERM handler until that child returns,
# so the polite signal frequently does nothing at all and the escalation is the
# path actually taken. Configurable for the same reason the bound above is —
# spend ten seconds of every CI run proving that a sleep ignores TERM.
: ${CARR_LOCK_TERM_GRACE_SECONDS:=10}

CARR_LOCK_DIR="${CARR_LOCK_DIR:-$HOME/carr-system/out/locks}"
CARR_LOCK_PATH=""

# carr_lock_age_seconds <lock-dir> — seconds since the holder claimed it, or ""
# when that cannot be established. UNKNOWN IS NEVER TREATED AS OLD: a lock whose
# `since` file is missing or unreadable has not been shown to be stale, and
# breaking one on an absence of evidence is how a healthy run gets killed.
# BOTH DATE DIALECTS, and this is not defensive padding. `date -j -f` is BSD and
# is what Joe's Mac has, where the chain actually runs; `date -d` is GNU and is
# what ubuntu-latest has, where ops/ci.sh's strict check actually runs. Parsing
# only the BSD way makes this function return nothing on the runner, which makes
# the bound silently inert there and the wedge test red in CI while green on the
# Mac — the exact green-locally/red-in-strict split the 18% failure rate is made
# of. Whichever dialect answers first wins; if neither does, the age is unknown.
carr_lock_age_seconds() {
  local lock="$1" since born now
  since="$(cat "$lock/since" 2>/dev/null)" || return 0
  [ -n "$since" ] || return 0
  born="$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$since" '+%s' 2>/dev/null)" \
    || born="$(date -u -d "$since" '+%s' 2>/dev/null)" \
    || return 0
  # UNKNOWN IS NEVER TREATED AS OLD (see above): a non-numeric answer is no
  # answer, and breaking a lock on one would kill a healthy run.
  case "$born" in ''|*[!0-9]*) return 0 ;; esac
  now="$(date -u '+%s')"
  print -r -- "$(( now - born ))"
  return 0
}

carr_take_lock() {              # carr_take_lock <name> -> 0 took it, 1 held elsewhere
  local name="$1"
  local lock="$CARR_LOCK_DIR/carr-$name.lock"
  mkdir -p "$CARR_LOCK_DIR" 2>/dev/null

  if ! mkdir "$lock" 2>/dev/null; then
    local holder
    holder="$(cat "$lock/pid" 2>/dev/null)"
    # A pid we can signal with 0 is alive and holds this lock. A pid we cannot
    # is gone, so the lock is a leftover rather than a live claim.
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
      local age; age="$(carr_lock_age_seconds "$lock")"
      if [ -n "$age" ] && [ "$age" -gt "$CARR_LOCK_STALE_AFTER_SECONDS" ]; then
        # WEDGED, and reported as such rather than reclaimed quietly. This IS a
        # failure — an unattended chain that stops making progress for an hour
        # and a half is a night nobody got — so the caller's log carries a word
        # no healthy run ever prints.
        print -r -- "WEDGED  '$name' has been held by live pid $holder for ${age}s, past the ${CARR_LOCK_STALE_AFTER_SECONDS}s bound — breaking the lock and stopping that run"
        # TERM first, and the escalation is not a formality: this chain's steps
        # spawn children under a wall-clock wrapper, and a holder blocked in a
        # syscall no signal handler runs during will not go on TERM alone.
        kill -TERM "$holder" 2>/dev/null
        local waited=0
        while [ "$waited" -lt "$CARR_LOCK_TERM_GRACE_SECONDS" ] && kill -0 "$holder" 2>/dev/null; do
          sleep 1
          waited=$((waited + 1))
        done
        if kill -0 "$holder" 2>/dev/null; then
          print -r -- "WEDGED  pid $holder ignored TERM for ${waited}s — sending KILL"
          kill -KILL "$holder" 2>/dev/null
          sleep 1
        fi
        rm -rf "$lock" 2>/dev/null
        if ! mkdir "$lock" 2>/dev/null; then
          print -r -- "LOCKED  another '$name' run claimed the broken lock first (held by pid $(cat "$lock/pid" 2>/dev/null || print -r -- '?')) — this run is a no-op"
          return 1
        fi
        print -r -- "$$" > "$lock/pid"
        date -u '+%Y-%m-%dT%H:%M:%SZ' > "$lock/since"
        CARR_LOCK_PATH="$lock"
        return 0
      fi
      print -r -- "LOCKED  another '$name' run is in progress (held by pid $holder since $(cat "$lock/since" 2>/dev/null || print -r -- 'unknown')${age:+, ${age}s ago}) — this run is a no-op"
      return 1
    fi
    print -r -- "STALE   '$name' lock left behind by pid ${holder:-unknown}, which is no longer running — reclaiming it"
    rm -rf "$lock" 2>/dev/null
    if ! mkdir "$lock" 2>/dev/null; then
      # Lost the race to reclaim: somebody else got there first, and they are live.
      print -r -- "LOCKED  another '$name' run reclaimed the stale lock first (held by pid $(cat "$lock/pid" 2>/dev/null || print -r -- '?')) — this run is a no-op"
      return 1
    fi
  fi

  print -r -- "$$" > "$lock/pid"
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$lock/since"
  CARR_LOCK_PATH="$lock"
  return 0
}

# RELEASE ONLY WHAT WE STILL HOLD. This used to remove CARR_LOCK_PATH
# unconditionally, which was safe while the only way to lose a lock was to exit.
# The bound above introduced a second way: a wedged holder is TERMed, and its own
# exit trap then runs while the breaker may already have created a NEW lock
# directory at the same path. An unconditional rm there deletes the breaker's
# claim and leaves two chains running against one database — precisely the
# failure this helper exists to prevent, reintroduced by the fix for a different
# one. Re-reading the pid file is the check: the directory at that path is ours
# only while it still names us.
carr_release_lock() {
  if [ -n "$CARR_LOCK_PATH" ]; then
    local owner
    owner="$(cat "$CARR_LOCK_PATH/pid" 2>/dev/null)"
    if [ "$owner" = "$$" ]; then
      rm -rf "$CARR_LOCK_PATH" 2>/dev/null
    else
      print -r -- "RELEASE '$CARR_LOCK_PATH' now names pid ${owner:-none}, not $$ — another run took it over; leaving it alone" >&2
    fi
  fi
  CARR_LOCK_PATH=""
  return 0
}
