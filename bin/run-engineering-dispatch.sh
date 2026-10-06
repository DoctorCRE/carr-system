#!/bin/zsh
# Invoke the Engineering Passport controller from the existing room-bridge
# wake.  It is not a scheduler: jobs enter only via MCP admission and this
# command claims only the fixed engineering definition.

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
NODE="/opt/homebrew/opt/node@22/bin/node"
PYTHON="$REPO/.venv/bin/python"
CONTROLLER_ENV_FILE="${CARR_ENGINEERING_CONTROLLER_ENV_FILE:-$HOME/.config/carr/engineering-controller.env}"
NOT_HOST_MARKER="${CARR_ENGINEERING_CONTROLLER_NOT_HOST_MARKER:-$HOME/.config/carr/engineering-controller.not-this-host}"

# One Mac holds the controller bearer: the Worker's ENGINEERING_CONTROLLER_TOKENS
# map accepts exactly one token.  Every Mac's room bridge still wakes this
# command, so a Mac that is deliberately NOT the controller host says so with
# this marker (its contents name where the controller runs) and answers with an
# explicit no-claim readback instead of failing every cycle.  Without the
# marker, a missing credential still fails loudly; with both, the machine
# contradicts itself and is refused.  Checked before anything else because it
# touches no credential.
if [[ -e "$NOT_HOST_MARKER" ]]; then
  if [[ -e "$CONTROLLER_ENV_FILE" ]]; then
    print -ru2 -- "engineering-dispatch: this Mac holds the controller credential and is marked not-this-host; remove one"
    exit 78
  fi
  print -r -- '{"ok":true,"claimed":0,"completed":0,"results":[],"host":"not_controller_host"}'
  exit 0
fi

# The controller bearer is a separate, Worker-only credential.  Read it as a
# literal from its own 0600 file when launchd did not provide it; never source
# the file and never print a value from it.  The issuer login slots are not
# accepted keys here, so this process can hold jobs proof plus the controller
# bearer, but never an ownership issuer credential.
load_controller_config() {
  local line key value mode
  [[ -n "${CARR_ENGINEERING_WORKER_URL:-}" && -n "${CARR_ENGINEERING_CONTROLLER_TOKEN:-}" ]] && return 0
  [[ -f "$CONTROLLER_ENV_FILE" && -r "$CONTROLLER_ENV_FILE" ]] || return 78
  mode="$(/usr/bin/stat -f '%Lp' "$CONTROLLER_ENV_FILE" 2>/dev/null || true)"
  [[ "$mode" == <-> ]] || mode="$(/usr/bin/stat -c '%a' "$CONTROLLER_ENV_FILE" 2>/dev/null || true)"
  [[ "$mode" == <-> && $(( 8#$mode & 077 )) -eq 0 ]] || return 78
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || return 78
    key="${line%%=*}"; value="${line#*=}"
    [[ "$key" == "CARR_ENGINEERING_WORKER_URL" || "$key" == "CARR_ENGINEERING_CONTROLLER_TOKEN" ]] || return 78
    [[ "$value" != *'$(' && "$value" != *'`'* && -n "$value" ]] || return 78
    if [[ "$value" == \'*\' ]]; then value="${value#\'}"; value="${value%\'}"
    elif [[ "$value" == \"*\" ]]; then value="${value#\"}"; value="${value%\"}"; fi
    typeset -gx "$key=$value"
  done < "$CONTROLLER_ENV_FILE"
  [[ -n "${CARR_ENGINEERING_WORKER_URL:-}" && -n "${CARR_ENGINEERING_CONTROLLER_TOKEN:-}" ]] || return 78
}

# Resolve the tracked, fixed implementation and dedicated desk before any
# credential is loaded.  Neither an ambient PATH nor a CARR_* override can
# choose code or a target that will later receive the jobs capability.
[[ -x "$NODE" ]] || { print -ru2 -- "engineering-dispatch: fixed Node 22 executable is required"; exit 78; }
[[ -x "$PYTHON" ]] || { print -ru2 -- "engineering-dispatch: repository Python is required"; exit 78; }
# The bridge consumes stdout as one exact JSON controller readback. The
# preflight's successful desk description would create a second JSON document
# and make the bridge reject a healthy response as malformed. Refusals still
# reach stderr and retain the pre-credential fail-closed boundary.
"$PYTHON" "$REPO/tools/room-bridge/engineering_dispatch_adapter.py" --preflight >/dev/null

load_controller_config || { print -ru2 -- "engineering-dispatch: isolated Worker controller configuration is required"; exit 78; }

source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL
[[ -n "${CARR_DB_JOBS_URL:-}" ]] || { print -ru2 -- "engineering-dispatch: CARR_DB_JOBS_URL is required"; exit 78; }
[[ -n "${CARR_ENGINEERING_WORKER_URL:-}" ]] || { print -ru2 -- "engineering-dispatch: CARR_ENGINEERING_WORKER_URL is required"; exit 78; }
[[ -n "${CARR_ENGINEERING_CONTROLLER_TOKEN:-}" ]] || { print -ru2 -- "engineering-dispatch: CARR_ENGINEERING_CONTROLLER_TOKEN is required"; exit 78; }

# The controller receives the jobs credential; its Python adapter builds a new
# allowlisted environment before it starts Codex, so no database credential can
# reach the agent process.
exec env -i HOME="$HOME" PATH="$PATH" LANG="${LANG:-C}" TMPDIR="${TMPDIR:-/tmp}" \
  CARR_DB_JOBS_URL="$CARR_DB_JOBS_URL" \
  CARR_ENGINEERING_WORKER_URL="$CARR_ENGINEERING_WORKER_URL" \
  CARR_ENGINEERING_CONTROLLER_TOKEN="$CARR_ENGINEERING_CONTROLLER_TOKEN" \
  "$NODE" "$REPO/mcp-server/bin/run-engineering-dispatch.mjs"
