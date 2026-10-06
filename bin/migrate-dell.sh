#!/bin/zsh
# migrate-dell.sh — ONE COMMAND. Brings Dell's Mac onto the new system.
#
# Joe, 2026-08-09: "i want to get on his laptop this week (maybe tuesday) and
# just say 'do the migration' and it just happens. no human steps, no permission
# needed. it just works"
#
# So the design constraints are absolute, and every one of them is checked by
# ops/migrate-dell-selftest.py rather than trusted:
#   NO sudo.            The gate hardening was removed 2026-08-09 precisely so
#                       machine setup would never need a password again.
#   NO credentials.     No token is read, written, or prompted for.
#   NO prompts.         Nothing waits on a human. stdin is closed on every child.
#   IDEMPOTENT.         Safe to run twice, or to re-run after a partial failure.
#   FAIL LOUD.          Any step that fails aborts with a non-zero exit and says
#                       what to do. A half-migrated machine that reports success
#                       is the worst possible outcome.
#
# WHAT IT DOES NOT DO, on purpose: it does not touch the record layer. Marking
# Dell's inbound items acknowledged is a VERB call, and verbs belong to the
# session driving this, not to a shell script. The script prints exactly which
# ones to close and the session does it — see THE SESSION'S HALF at the bottom.
#
# WHY A SCRIPT AND NOT A CHECKLIST. Every step below is currently a hand
# instruction sitting in Dell's inbound queue, where it has aged since 2026-08-06
# without being done. A checklist that has not been actioned in three days is not
# a plan. Rule a8c55a47 — a manual path and an automated path that do the same
# job must be the same code — so this script IS the migration, and the queue item
# becomes a pointer to it.
#
#   bin/migrate-dell.sh            # dry run: report what would change
#   bin/migrate-dell.sh --apply    # do it
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

# DEFINED BEFORE ANY CALLER, and that ordering is load-bearing rather than
# stylistic. `say` is also /usr/bin/say, macOS text-to-speech. The first version
# of --preflight sat ABOVE these definitions, so every `say` line in it fell
# through to the system binary: the run printed nothing, exited 0, and READ
# ITSELF ALOUD through the laptop speakers. Joe heard it before the transcript
# showed it. Keep every helper above the first caller.
ok=0; warn=0; fail=0
say()  { print -r -- "$@"; }
good() { print -r -- "  ok    $*"; ok=$((ok+1)); }
note() { print -r -- "  note  $*"; warn=$((warn+1)); }
bad()  { print -r -- "  FAIL  $*"; fail=$((fail+1)); }
step() { print -r -- ""; print -r -- "── $* ──────────────────────────────"; }

# ── PREFLIGHT ───────────────────────────────────────────────────────────────
# Run on the PRIMARY machine, before the visit: "is what Dell will pull actually
# going to work?" A dry run here cannot answer that — every plist already
# matches, every gate is already installed, the venv already exists — so it
# reports 13 ok / 0 fail on a packet that fails on a clean Mac. The 2026-08-10
# audit found five such defects that way, including a non-executable script that
# ended the migration at the first character.
#
# So: clone what origin/main ACTUALLY holds into a scratch HOME, at the same
# ~/carr-system path the gates resolve, and run the real verify suite there.
# Same code as the migration itself (rule a8c55a47), which is why this lives in
# this file rather than in a checklist somebody runs differently each time.
#
# Two results are EXPECTED here and are not defects, both artefacts of the
# scratch location rather than of the packet:
#   sh-destructive — the guard waives recursive deletes inside SAFE_ZONES, and
#                    /private/tmp plus "scratchpad" are both safe zones. On a
#                    real ~/carr-system the same case denies.
#   uncommitted gates — anything still in the working tree here is invisible to
#                    the clone, which is the point: it is exactly what Dell gets.
if [ "${1:-}" = "--preflight" ]; then
  # Resolved from the REAL home, before HOME is redirected at the scratch machine.
  REAL_VAULT="${CARR_VAULT:-}"
  if [ -z "$REAL_VAULT" ]; then
    for c in "$HOME"/Library/CloudStorage/GoogleDrive-*/"My Drive"/"CARR AI"; do
      [ -d "$c" ] && { REAL_VAULT="$c"; break; }
    done
  fi
  TMP="${TMPDIR:-/tmp}/carr-preflight-$$"
  trap 'rm -rf "$TMP"' EXIT INT TERM
  mkdir -p "$TMP/home/Library/LaunchAgents" "$TMP/home/.claude"
  print -r -- '{}' > "$TMP/home/.claude/settings.json"
  say "PREFLIGHT — cloning origin/main into a scratch machine"
  say "this answers 'what will Dell actually get', which a dry run here cannot"
  say ""
  if ! git -C "$REPO" fetch --quiet </dev/null 2>/dev/null; then
    say "  note  could not fetch; testing the local main instead"
  fi
  PREFLIGHT_REF="${CARR_PREFLIGHT_REF:-origin/main}"
  if ! REF=$(git -C "$REPO" rev-parse --verify --quiet "$PREFLIGHT_REF^{commit}"); then
    say "  FAIL  preflight ref '$PREFLIGHT_REF' is not a commit"
    exit 1
  fi
  if ! git clone --quiet --no-local "$REPO" "$TMP/home/carr-system" </dev/null 2>&1; then
    say "  FAIL  clone failed"; exit 1
  fi
  # A local clone transfers branch refs, not the source repo's remote-tracking
  # refs. After a GitHub merge, origin/main can therefore name an object the
  # clone did not receive. The old checkout suppressed that failure and tested
  # the feature-branch HEAD while labelling it origin/main. Fetch the selected
  # source ref explicitly, compare its object ID, and refuse any mismatch.
  if ! git -C "$TMP/home/carr-system" fetch --quiet "$REPO" "$PREFLIGHT_REF" </dev/null 2>/dev/null; then
    say "  FAIL  could not fetch exact preflight ref '$PREFLIGHT_REF' into scratch clone"
    exit 1
  fi
  FETCHED=$(git -C "$TMP/home/carr-system" rev-parse FETCH_HEAD 2>/dev/null || print -r -- "")
  if [ "$FETCHED" != "$REF" ]; then
    say "  FAIL  requested $REF but scratch fetch returned ${FETCHED:-nothing}"
    exit 1
  fi
  if ! git -C "$TMP/home/carr-system" checkout --quiet --detach "$REF" </dev/null 2>/dev/null; then
    say "  FAIL  could not check out exact preflight commit $REF"
    exit 1
  fi
  CHECKED_OUT=$(git -C "$TMP/home/carr-system" rev-parse HEAD 2>/dev/null || print -r -- "")
  if [ "$CHECKED_OUT" != "$REF" ]; then
    say "  FAIL  scratch checkout is ${CHECKED_OUT:-nothing}, expected $REF"
    exit 1
  fi
  git -C "$TMP/home/carr-system" config user.email "preflight@example.com"
  PFAIL=0

  say "testing $(git -C "$TMP/home/carr-system" rev-parse --short HEAD) ($PREFLIGHT_REF)"
  MODE=$(git -C "$TMP/home/carr-system" ls-files -s bin/migrate-dell.sh | cut -d' ' -f1)
  if [ "$MODE" = "100755" ]; then
    say "  ok    bin/migrate-dell.sh is executable as pulled"
  else
    say "  FAIL  bin/migrate-dell.sh is mode $MODE — Dell gets 'permission denied'"
    say "        fix: git update-index --chmod=+x bin/migrate-dell.sh && commit"
    PFAIL=$((PFAIL+1))
  fi

  say ""
  say "  gates, as Dell will run them:"
  for t in guard conduct-gate escalation-gate gate-edit-gate git-writer-gate \
           context-handoff-gate corpus-render-gate config-as-code migrate-dell; do
    F="$TMP/home/carr-system/ops/$t-selftest.py"
    [ -f "$F" ] || { print -r -- "    note  $t — not in the clone"; continue; }
    # CARR_VAULT is passed through deliberately. The scratch HOME has no Drive
    # mount, and the corpus-render deny set resolves its roots by globbing
    # ~/Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI — so without this the
    # deny set comes back empty, a Bash tee onto a render is "allowed", and the
    # gate reports a failure that exists only because the test machine has no
    # vault. Handing it the real one (read-only; the selftest writes nothing
    # there) tests the gate against a real corpus instead of waiving the case.
    # corpus-render is MACHINE-ANCHORED and so gets the real HOME. Its deny set
    # is the realpath of files under My Drive/.claude and the vault — absolute
    # paths that exist on a real Mac and nowhere in a scratch tree — so under a
    # fake HOME the set resolves to targets the clone's own test paths can never
    # match, and a Bash tee comes back "allowed" for a gate that is fine. What
    # preflight needs to know is whether the CODE on origin/main works on a real
    # machine, so it runs the clone's selftest against this one. Every other gate
    # keeps the scratch HOME, which is what makes them a fresh-machine test.
    PF_HOME="$TMP/home"
    [ "$t" = "corpus-render-gate" ] && PF_HOME="$HOME"
    if (cd "$TMP/home/carr-system" && HOME="$PF_HOME" CARR_VAULT="$REAL_VAULT" \
         python3 "$F" </dev/null >/tmp/pf.log 2>&1); then
      print -r -- "    ok    $t"
    elif [ "$t" = "gate-edit-gate" ] && grep -q "FAILURES: sh-destructive" /tmp/pf.log \
         && [ "$(grep -c FAILURES /tmp/pf.log)" = "1" ]; then
      print -r -- "    ok    $t (sh-destructive waived: scratch is inside a SAFE_ZONE)"
    else
      print -r -- "    FAIL  $t — $(tail -1 /tmp/pf.log | tr -s ' ')"
      PFAIL=$((PFAIL+1))
    fi
  done

  say ""
  say "  actual installer, with empty Claude settings and no Codex client:"
  if (cd "$TMP/home/carr-system" && HOME="$TMP/home" CARR_VAULT="$REAL_VAULT" \
       python3 ops/config-as-code.py install </dev/null >/tmp/pf-install.log 2>&1); then
    print -r -- "    ok    Claude-only config install dry run"
  else
    print -r -- "    FAIL  Claude-only config install — $(tail -1 /tmp/pf-install.log | tr -s ' ')"
    PFAIL=$((PFAIL+1))
  fi

  # The defect class that caused this audit: a gate green here only because of
  # working-tree changes nobody pushed. Name the files rather than the count.
  say ""
  UNPUSHED=$(git -C "$REPO" status --porcelain --untracked-files=no -- hooks ops 2>/dev/null \
             | awk '{print $2}' | grep -E 'hooks/.*\.py$|selftest\.py$' || true)
  if [ -n "$UNPUSHED" ]; then
    say "  FAIL  gate work exists here that Dell will NOT get:"
    print -r -- "$UNPUSHED" | sed 's/^/          /'
    say "        a gate and its selftest must ship together — commit and push these"
    PFAIL=$((PFAIL+1))
  else
    say "  ok    no uncommitted gate or selftest work"
  fi

  say ""
  if [ $PFAIL -gt 0 ]; then
    say "PREFLIGHT FAILED — $PFAIL item(s). Dell's migration would not be clean."
    exit 1
  fi
  say "PREFLIGHT CLEAN — the packet at $PREFLIGHT_REF works on a fresh Mac."
  say "On Dell's machine: start Claude Code in ~/carr-system and type: ready for migration"
  exit 0
fi

# An apply run always leaves a machine-readable receipt, including on failure.
# The session uses this as the boundary between machine work and record writes:
# only the exact success status below permits the latter. Atomic rename prevents
# a killed process from leaving a half-written receipt that looks authoritative.
RECEIPT="$REPO/out/dell-migration-receipt.json"
migration_receipt() {
  local rc="${1:-1}"
  trap - EXIT
  [ $APPLY -eq 1 ] || return 0
  mkdir -p "$REPO/out" 2>/dev/null || return 0
  # `status` is a special read-only parameter in zsh (the script's shell), so
  # a local of that name aborts the EXIT receipt path on Dell's Mac. Keep the
  # JSON field name but use an ordinary local variable.
  local receipt_status="migration_incomplete"
  [ "$rc" -eq 0 ] && receipt_status="machine_migrated_pending_record_closeout"
  local commit branch tmp
  commit=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || print -r -- "unknown")
  branch=$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || print -r -- "unknown")
  tmp="$RECEIPT.tmp.$$"
  /usr/bin/python3 -c '
import datetime, json, sys
path, status, commit, branch, rc, ok, note, fail = sys.argv[1:]
receipt = {
  "schema_version": 1,
  "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
  "status": status,
  "exit_code": int(rc),
  "source_commit": commit,
  "source_branch": branch,
  "counts": {"ok": int(ok), "note": int(note), "fail": int(fail)},
  "machine_checks": [
    "preconditions", "fast_forward_only", "python_environment",
    "config_as_code_install", "derived_fetch_allowlist", "gate_selftests",
    "config_drift_check"
  ],
  "record_closeout": {
    "performed_by_script": False,
    "required_after_success": ["A15", "A17"],
    "must_remain_open": ["A11", "A12", "A13", "A16"]
  },
  "live_identity_verification_required": True,
  "logs": [
    "/tmp/mig-pull.log", "/tmp/mig-venv.log", "/tmp/mig-pip.log",
    "/tmp/mig-hooks.log", "/tmp/mig-allow.log", "/tmp/mig-check.log",
    "/tmp/mig-drift.log"
  ]
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(receipt, handle, indent=2, sort_keys=True)
    handle.write("\n")
' "$tmp" "$receipt_status" "$commit" "$branch" "$rc" "$ok" "$warn" "$fail" \
    && mv -f "$tmp" "$RECEIPT" \
    && say "receipt: $RECEIPT" \
    || { rm -f "$tmp"; say "  FAIL  could not write migration receipt: $RECEIPT"; }
  return 0
}
trap 'migration_receipt $?' EXIT

PY="python3"
[ -x "$REPO/.venv/bin/python" ] && PY="$REPO/.venv/bin/python"

say "CARR migration — Dell's machine"
say "repo: $REPO"
[ $APPLY -eq 1 ] && say "mode: APPLY" || say "mode: DRY RUN (re-run with --apply to execute)"

# ── 1. PRECONDITIONS ────────────────────────────────────────────────────────
step "1. preconditions"
[ -d "$REPO/.git" ] && good "repo present" || { bad "no git repo at $REPO — clone carr-system first"; exit 1; }
command -v git >/dev/null && good "git" || bad "git missing"
command -v $PY  >/dev/null && good "python ($PY)" || bad "python3 missing"
[ -f "$HOME/.claude/settings.json" ] && good "Claude settings present" \
  || note "no ~/.claude/settings.json yet — install will create the hooks block"

# THE CLONE PATH IS LOAD-BEARING, which was not obvious until the 2026-08-10
# fresh-machine audit measured it. hooks/git-writer-gate.py:59 resolves
# ~/carr-system to decide whether the tree is dirty, and gate_paths' protected
# patterns match on /carr-system/. A clone at ~/dev/carr-system installs, passes
# its own selftests, and then guards nothing — the green-but-guarding-zero-paths
# failure this audit found twice. Refuse it here rather than let it pass.
if [ "$REPO" != "$HOME/carr-system" ]; then
  bad "repo is at $REPO but the gates resolve ~/carr-system — move the clone:"
  say "          mv \"$REPO\" \"$HOME/carr-system\" && \"$HOME/carr-system/bin/migrate-dell.sh\" --apply"
else
  good "clone is at ~/carr-system (the path the gates resolve)"
fi

# A dirty tree can silently lose the other writer's work when we pull — the
# 2026-08-09 incident, and the reason for the two-writer rule on the git tree.
# But refusing on ANY dirty file is too blunt: this machine had 9 unrelated
# modified files from a concurrent session and the script aborted, which on
# Dell's Mac would mean "it just works" doesn't. So refuse only on a REAL
# conflict — a file that is both locally modified AND changed upstream. Anything
# else is another lane's work that a fast-forward will not touch.
git -C "$REPO" fetch --quiet </dev/null 2>/dev/null || true
DIRTY_FILES=$(git -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null | awk '{print $2}')
UPSTREAM=$(git -C "$REPO" rev-parse --abbrev-ref '@{u}' 2>/dev/null || echo "")
CONFLICTS=""

# INCOMING means "changed upstream SINCE WE DIVERGED", which is a merge-base
# diff. A plain two-dot `git diff HEAD upstream` is the symmetric difference of
# two trees, so a file this machine ADDED locally shows up as "changed upstream"
# even though nothing is arriving for it — which is exactly how this check
# flagged migrate-dell.sh against itself and aborted its own test run. If there
# is nothing to pull, no conflict is possible.
if [ -n "$UPSTREAM" ] && [ -n "$DIRTY_FILES" ]; then
  BASE=$(git -C "$REPO" merge-base HEAD "$UPSTREAM" 2>/dev/null)
  if [ -n "$BASE" ]; then
    INCOMING=$(git -C "$REPO" diff --name-only "$BASE" "$UPSTREAM" 2>/dev/null)
    if [ -n "$INCOMING" ]; then
      CONFLICTS=$(print -r -- "$DIRTY_FILES" | while read -r f; do
        [ -n "$f" ] && print -r -- "$INCOMING" | grep -qxF "$f" && print -r -- "$f"
      done)
    fi
  fi
fi
if [ -n "$CONFLICTS" ]; then
  bad "these files are modified locally AND changed upstream — commit or stash them first:"
  print -r -- "$CONFLICTS" | sed 's/^/          /'
elif [ -n "$DIRTY_FILES" ]; then
  note "$(print -r -- "$DIRTY_FILES" | wc -l | tr -d ' ') unrelated modified file(s) — untouched by a fast-forward, proceeding"
else
  good "working tree clean"
fi
[ $fail -gt 0 ] && { say ""; say "ABORTED — $fail precondition(s) failed."; exit 1; }

# ── 2. PULL ─────────────────────────────────────────────────────────────────
step "2. pull latest"
BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$BRANCH" = "main" ] && good "on main" || note "on '$BRANCH', not main — pulling that branch"
# THREE STATES, NOT TWO — found by running --apply on a machine that had its own
# unpushed commits. A bare `git pull --ff-only` aborts with "Not possible to
# fast-forward" whenever the local repo is AHEAD, which is not a failure at all;
# it just means there is nothing to pull. The first version reported that as
# MIGRATION INCOMPLETE and would have done the same on Dell's Mac the moment he
# had one local commit.
if [ -z "$UPSTREAM" ]; then
  note "no upstream tracking branch — skipping pull"
else
  BEHIND=$(git -C "$REPO" rev-list --count HEAD.."$UPSTREAM" 2>/dev/null || echo 0)
  AHEAD=$(git -C "$REPO" rev-list --count "$UPSTREAM"..HEAD 2>/dev/null || echo 0)
  if [ "$BEHIND" -eq 0 ] && [ "$AHEAD" -eq 0 ]; then
    good "already up to date"
  elif [ "$BEHIND" -eq 0 ]; then
    good "$AHEAD local commit(s) not yet pushed, nothing to pull — fine"
  elif [ "$AHEAD" -eq 0 ]; then
    if [ $APPLY -eq 1 ]; then
      if git -C "$REPO" merge --ff-only "$UPSTREAM" </dev/null >/tmp/mig-pull.log 2>&1; then
        good "fast-forwarded $BEHIND commit(s) to $(git -C "$REPO" rev-parse --short HEAD)"
      else
        bad "fast-forward failed — $(tail -1 /tmp/mig-pull.log)"
      fi
    else
      good "would fast-forward $BEHIND commit(s)"
    fi
  else
    # Diverged. Never auto-merge or rebase another writer's history.
    bad "branch has diverged ($AHEAD local, $BEHIND remote) — reconcile by hand before migrating"
  fi
fi

# ── 3. HOOKS ────────────────────────────────────────────────────────────────
# This is the whole of the old hand-instruction "change the guard-unattended
# matcher from Bash to Bash|WebFetch". The repo's ops/config/hooks.json already
# carries the correct matcher plus every gate built since, so installing it
# replaces that manual edit entirely — and brings the conduct gates, the
# escalation gate, the git-writer gate and the integrity check with it.
step "3. python environment"
# THE FRESH-MACHINE BLOCKER, found in the 2026-08-10 audit. bin/nightly.sh runs
# ./.venv/bin/python explicitly on eight of its steps, two launchd jobs name that
# interpreter in their ProgramArguments, and NOTHING in this repo has ever
# created it — it exists on the primary machine only because it was made by hand
# long ago. On a fresh clone every one of those fails silently at its first fire.
# The gate selftests themselves are stdlib-only and would have passed, so the
# migration would have reported success on a machine whose scheduled work could
# not run. That is precisely the "reports success while half-migrated" outcome
# this script was written to prevent.
# THE INTERPRETER FLOOR, added 2026-08-19 after this bit shipped a machine that
# looked migrated and could not run its own checks. This block used to accept
# ANY interpreter: it said "venv present" for whatever was on disk and built new
# ones with a bare `python3 -m venv`. On Dell's Mac that meant the Command Line
# Tools' Python 3.9.6, and the repo is written for 3.10+ — `dict | None`
# annotations are evaluated at definition time and raise TypeError on 3.9, so
# THIRTY-SIX gate selftests could not even be imported, mypy could not be
# installed at all (requirements.txt pins it to python_version >= "3.10"), and
# tomllib was missing. Pre-push checks were red on a clean trunk, so every push
# from that machine needed CARR_SKIP_CI, which is how a skip flag becomes
# routine. The floor is named once here and enforced on BOTH paths — an existing
# venv is version-checked, not merely existence-checked, because "it is there"
# was exactly the wrong question.
PY_MIN_MINOR=10

py_meets_floor() {  # py_meets_floor <interpreter>
  "$1" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, $PY_MIN_MINOR) else 1)" >/dev/null 2>&1
}

py_version_of() { "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo unknown; }

find_base_python() {  # newest-first; prints a path, or nothing
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 \
           "$HOME/.local/bin/python3.12" python3; do
    p="$(command -v "$c" 2>/dev/null || true)"
    [ -z "$p" ] && [ -x "$c" ] && p="$c"
    if [ -n "$p" ] && py_meets_floor "$p"; then printf '%s\n' "$p"; return 0; fi
  done
  return 1
}

VENV_PY="$REPO/.venv/bin/python"
VENV_OK=0
if [ -x "$VENV_PY" ] && py_meets_floor "$VENV_PY"; then
  VENV_OK=1
  good "venv present (Python $(py_version_of "$VENV_PY"))"
elif [ -x "$VENV_PY" ]; then
  bad "venv is Python $(py_version_of "$VENV_PY"), below the 3.$PY_MIN_MINOR floor this repo requires — move it aside and re-run so it is rebuilt"
elif [ $APPLY -eq 1 ]; then
  BASE_PY="$(find_base_python || true)"
  if [ -z "$BASE_PY" ]; then
    # Self-serve rather than stopping: uv installs a standalone CPython into the
    # user's home with no admin password and no system change. Only reached when
    # the machine genuinely has nothing recent enough.
    if python3 -m pip install --user --quiet uv </dev/null >>/tmp/mig-venv.log 2>&1 \
       && "$HOME/Library/Python/3.9/bin/uv" python install 3.12 </dev/null >>/tmp/mig-venv.log 2>&1; then
      BASE_PY="$(find_base_python || true)"
      [ -n "$BASE_PY" ] && good "installed Python $(py_version_of "$BASE_PY") (user-local, no admin needed)"
    fi
  fi
  if [ -z "$BASE_PY" ]; then
    bad "no Python 3.$PY_MIN_MINOR+ available and none could be installed — see /tmp/mig-venv.log"
  elif "$BASE_PY" -m venv "$REPO/.venv" </dev/null >>/tmp/mig-venv.log 2>&1; then
    VENV_OK=1
    good "venv created on Python $(py_version_of "$VENV_PY")"
  else
    bad "venv creation failed — see /tmp/mig-venv.log"
  fi
else
  good "would create .venv on Python 3.$PY_MIN_MINOR or newer"
fi

if [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
  if $PY -c "import psycopg, openpyxl, PIL" >/dev/null 2>&1; then
    good "python deps present"
  elif [ $APPLY -eq 1 ]; then
    # The only step that touches the network. Slowest part of the migration.
    if $PY -m pip install -q --disable-pip-version-check \
         -r "$REPO/requirements.txt" </dev/null >/tmp/mig-pip.log 2>&1; then
      good "requirements installed"
    else
      bad "pip install failed — see /tmp/mig-pip.log"
    fi
  else
    note "would install requirements.txt (needs network, ~1 min)"
  fi
fi

step "4. install gates"
if [ $APPLY -eq 1 ]; then
  if $PY "$REPO/ops/config-as-code.py" install --apply </dev/null >/tmp/mig-hooks.log 2>&1; then
    good "hooks installed (all other settings keys preserved)"
  else
    bad "hook install failed — see /tmp/mig-hooks.log"
  fi
else
  if $PY "$REPO/ops/config-as-code.py" install </dev/null >/tmp/mig-hooks.log 2>&1; then
    sed 's/^/        /' /tmp/mig-hooks.log | head -6
    good "would install the repo's hooks block"
  else
    sed 's/^/        /' /tmp/mig-hooks.log | head -6
    bad "hook install dry run failed — see /tmp/mig-hooks.log"
  fi
fi

# ── 4. DERIVED ALLOWLIST ────────────────────────────────────────────────────
# Machine-local and gitignored, so it does not exist on a fresh clone. Without
# it the egress guard falls back to its code list — safe, but client practice
# sites stay unreachable until the nightly runs. Generate it now instead.
step "5. derived fetch allowlist"
if [ -f "$REPO/ops/fetch-allowlist.py" ]; then
  if [ $APPLY -eq 1 ]; then
    if $PY "$REPO/ops/fetch-allowlist.py" </dev/null >/tmp/mig-allow.log 2>&1; then
      good "allowlist generated"
    else
      note "allowlist generation failed (guard falls back to its code list — safe) — /tmp/mig-allow.log"
    fi
  else
    good "would generate the record-derived host allowlist"
  fi
else
  note "ops/fetch-allowlist.py not present — skipping"
fi

# ── 5. VERIFY ───────────────────────────────────────────────────────────────
# Every gate is exercised by spawning the REAL hook, never by importing a
# function (rule a9ecd5b4: a check compares the artifact against what it should
# be). A migration that installs gates without proving they fire has proved
# nothing.
step "6. verify"
run_check() {
  local label="$1" file="$2"
  [ -f "$REPO/$file" ] || { note "$label — not present, skipped"; return; }
  if $PY "$REPO/$file" </dev/null >/tmp/mig-check.log 2>&1; then
    good "$label — $(tail -1 /tmp/mig-check.log | tr -s ' ')"
  else
    bad "$label FAILED — $(tail -1 /tmp/mig-check.log | tr -s ' ')"
  fi
}
run_check "egress guard"     "ops/guard-selftest.py"
run_check "conduct gate"     "ops/conduct-gate-selftest.py"
run_check "escalation gate"  "ops/escalation-gate-selftest.py"
run_check "gate-edit gate"   "ops/gate-edit-gate-selftest.py"
run_check "git-writer gate"  "ops/git-writer-gate-selftest.py"
# Added after the two gates below shipped (2026-08-10), later than this script.
# Step 3 installs every gate in the repo's hooks block, so a verify list that
# lags the hooks block certifies a machine it has not fully checked.
run_check "context-handoff"  "ops/context-handoff-gate-selftest.py"
run_check "corpus-render"    "ops/corpus-render-gate-selftest.py"

if $PY "$REPO/ops/config-as-code.py" check </dev/null >/tmp/mig-drift.log 2>&1; then
  good "config matches the repo (no drift)"
else
  if [ $APPLY -eq 1 ]; then
    bad "config drift remains after install — see /tmp/mig-drift.log"
  else
    note "config drift (expected before --apply)"
  fi
fi

# ── 6. REPORT ───────────────────────────────────────────────────────────────
step "result"
say "  $ok ok · $warn note · $fail fail"
say ""
if [ $fail -gt 0 ]; then
  say "MIGRATION INCOMPLETE — $fail step(s) failed. Nothing is half-applied that"
  say "cannot be fixed by re-running; this script is idempotent."
  exit 1
fi
if [ $APPLY -eq 0 ]; then
  say "Dry run clean. Re-run with --apply to migrate."
  exit 0
fi

cat <<'NEXT'
MACHINE IS MIGRATED. The gates are live immediately — Claude Code re-reads its
hooks per tool call, so every session already running on this Mac is covered
without a restart (verified 2026-08-09).

THE SESSION'S HALF — the driving session does these, not this script, because
they are record writes and records go through verbs:

  1. Require out/dell-migration-receipt.json to report
     machine_migrated_pending_record_closeout. Then call standing-context and
     verify its server-derived sponsor is Dell, its personal brain is
     dell-personal, and its shared/personal counts match the current generated
     fallback headers. Do not pass a partner, tenant, or capability selector.

  2. Fresh-read, close with a concrete verified outcome, and read back exactly:
       - A15 / fa0e6c92-8bc7-4e42-9970-0402914d6a19 (machine migration)
       - A17 / deb4357e-801f-49f6-bc6c-4c884e3e1f7c (doctrine-store readiness)
     Leave A11, A12 and A13 open: Dell must actually read and agree to those
     protocol changes. Leave A16 open because the dictation rig is unrelated.

  3. Run ./run.sh health and ops/config-as-code.py check, then tell Dell what
     changed on his machine in plain words, not item numbers.

The compiled shared and Dell-personal files remain the supported local fallback
through the 2026-08-21 cutoff. A live doctrine-store read does not retire them
early. Do not disconnect or reconnect the connector unless the deployed tool
registry proves that is required.
NEXT
exit 0
