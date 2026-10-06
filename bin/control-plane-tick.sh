#!/bin/zsh
# Feed due work to the CARR ledger from Joe's local edge node.
#
# launchd is only a wake-up adapter.  This wrapper neither owns recurrence nor
# invokes workflow code: ``tools/control-plane.py tick`` decides what is due,
# and the ledger serializes the work.  The LaunchAgent definition is versioned
# with this file but intentionally is not installed or enabled here.

set -u

EX_CONFIG=78
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_DIR="${CARR_CONTROL_PLANE_LOCK_DIR:-$REPO/out/control-plane-tick.lock}"
DB_ENV="${CARR_CONTROL_PLANE_DB_ENV:-$HOME/.config/carr/db.env}"
PROVIDER_ENV="${CARR_CONTROL_PLANE_PROVIDER_ENV:-$HOME/.config/carr/control-plane.env}"

# mkdir is atomic.  A second launchd wake is expected under slow provider or
# database conditions; it is an already-covered opportunity, not an error.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  print -ru2 -- "control-plane-tick: already active; skipping overlapping wake"
  exit 0
fi

release_lock() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap release_lock EXIT

# Read a local KEY=VALUE file without treating it as a shell program.  The
# tick adapter handles credentials, so ``source``/``eval`` would give a local
# config file arbitrary code execution before the worker's environment is
# scrubbed.  Values stay literal; shell expansion syntax is not accepted.
typeset -A PARSED_ENV
credential_file_mode() {
  local file="$1" mode

  # macOS ships BSD stat; Ubuntu ships GNU stat.  Do not treat an unsupported
  # format flag as a clean permission result: a credential file whose mode
  # cannot be determined must be refused.
  mode="$(/usr/bin/stat -f '%Lp' "$file" 2>/dev/null || true)"
  if [[ "$mode" != <-> ]]; then
    mode="$(/usr/bin/stat -c '%a' "$file" 2>/dev/null || true)"
  fi
  if [[ "$mode" != <-> ]]; then
    return 1
  fi
  print -r -- "$mode"
}

parse_env_file() {
  local file="$1" line key value mode

  if [[ ! -f "$file" || ! -r "$file" ]]; then
    print -ru2 -- "control-plane-tick: configuration file is not readable: $file"
    return 1
  fi
  mode="$(credential_file_mode "$file" || true)"
  if [[ "$mode" != <-> ]]; then
    print -ru2 -- "control-plane-tick: cannot determine credential file permissions for $file"
    return 1
  fi
  if (( (8#$mode & 077) != 0 )); then
    print -ru2 -- "control-plane-tick: refusing insecure credential file permissions on $file (require 0600)"
    return 1
  fi

  PARSED_ENV=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" != *=* ]]; then
      print -ru2 -- "control-plane-tick: malformed KEY=VALUE entry in $file"
      return 1
    fi
    key="${line%%=*}"
    value="${line#*=}"
    # db.env may quote a literal URI.  Remove one matching outer quote pair
    # without evaluating escapes, substitutions, or any shell syntax.
    if [[ "$value" == \'*\' ]]; then
      value="${value#\'}"
      value="${value%\'}"
    elif [[ "$value" == \"*\" ]]; then
      value="${value#\"}"
      value="${value%\"}"
    fi
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || [[ "$value" == *'$('* || "$value" == *'`'* ]]; then
      print -ru2 -- "control-plane-tick: malformed KEY=VALUE entry in $file"
      return 1
    fi
    if (( ${+PARSED_ENV[$key]} )); then
      print -ru2 -- "control-plane-tick: duplicate key $key in $file"
      return 1
    fi
    PARSED_ENV[$key]="$value"
  done < "$file"
}

# launchd does not inherit an interactive shell's environment.  Read only the
# jobs URL from the established local config. An explicitly supplied jobs URL
# wins so a managed launchd environment need not read a file at all.
jobs_url="${CARR_DB_JOBS_URL:-}"
if [[ -z "$jobs_url" && -e "$DB_ENV" ]]; then
  parse_env_file "$DB_ENV" || exit "$EX_CONFIG"
  jobs_url="${PARSED_ENV[CARR_DB_JOBS_URL]:-}"
fi
if [[ -z "$jobs_url" ]]; then
  print -ru2 -- "control-plane-tick: CARR_DB_JOBS_URL is required"
  exit "$EX_CONFIG"
fi

# Provider endpoints are model-neutral route adapters.  Read only the four
# registered adapter variables from the dedicated local file; unrelated shell
# state and every database credential remain outside the worker environment.
primary_url="${CARR_AI_ROUTE_PRIMARY_URL:-}"
primary_token="${CARR_AI_ROUTE_PRIMARY_TOKEN:-}"
secondary_url="${CARR_AI_ROUTE_SECONDARY_URL:-}"
secondary_token="${CARR_AI_ROUTE_SECONDARY_TOKEN:-}"
if [[ -e "$PROVIDER_ENV" ]]; then
  parse_env_file "$PROVIDER_ENV" || exit "$EX_CONFIG"
  [[ -z "$primary_url" ]] && primary_url="${PARSED_ENV[CARR_AI_ROUTE_PRIMARY_URL]:-}"
  [[ -z "$primary_token" ]] && primary_token="${PARSED_ENV[CARR_AI_ROUTE_PRIMARY_TOKEN]:-}"
  [[ -z "$secondary_url" ]] && secondary_url="${PARSED_ENV[CARR_AI_ROUTE_SECONDARY_URL]:-}"
  [[ -z "$secondary_token" ]] && secondary_token="${PARSED_ENV[CARR_AI_ROUTE_SECONDARY_TOKEN]:-}"
fi

PYTHON="$REPO/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 2>/dev/null || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  print -ru2 -- "control-plane-tick: python3 is required"
  exit "$EX_CONFIG"
fi

# Start the worker with a fresh environment.  Only the explicit jobs variable
# crosses this boundary; DATABASE_URL can never become a routine fallback.
# HOME/PATH are operational settings, not credentials.
#
# --mode auto resolves each due workflow to its own tier against the
# acceptance ladder ops.enqueue_job enforces (migration 0332): shadow always;
# canary once an accepted shadow receipt exists for that workflow and its
# canary contract is enabled; live once an accepted canary receipt exists, or
# once accepted shadow exists where the workflow's contract disables canary.
# This wrapper only wakes the ledger -- it never chooses a tier itself.
env -i PATH="$PATH" HOME="$HOME" CARR_DB_JOBS_URL="$jobs_url" \
  CARR_AI_ROUTE_PRIMARY_URL="$primary_url" \
  CARR_AI_ROUTE_PRIMARY_TOKEN="$primary_token" \
  CARR_AI_ROUTE_SECONDARY_URL="$secondary_url" \
  CARR_AI_ROUTE_SECONDARY_TOKEN="$secondary_token" \
  "$PYTHON" "$REPO/tools/control-plane.py" tick --mode auto
