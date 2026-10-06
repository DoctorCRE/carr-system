#!/bin/zsh
# Narrow credential loader for unattended routines.  This file is safe to source;
# it never evaluates ~/.config/carr/db.env.

carr_clear_routine_db_env() {
  unset DATABASE_URL CARR_DB_WRITER_URL CARR_DB_OWNER_URL CARR_DB_CADENCE_URL \
    CARR_DB_MATCHER_URL CARR_DB_EXPORTER_URL CARR_DB_JOBS_URL CARR_DB_BACKUP_URL \
    BACKUP_DATABASE_URL CARR_IMPORT_DB_URL PGHOST PGHOSTADDR PGPORT PGDATABASE \
    PGUSER PGPASSWORD PGSERVICE PGOPTIONS
}

carr_credential_file_mode() {
  local file="$1" mode
  mode="$(/usr/bin/stat -f '%Lp' "$file" 2>/dev/null || true)"
  [[ "$mode" == <-> ]] || mode="$(/usr/bin/stat -c '%a' "$file" 2>/dev/null || true)"
  [[ "$mode" == <-> ]] || return 1
  print -r -- "$mode"
}

# carr_load_routine_db_env KEY [KEY ...]
# Accept only simple KEY=VALUE lines for the named keys.  A routine never needs
# shell expansion in a credential file, and accepting it would turn config into
# executable code.  Unknown keys stay unread rather than being inherited.
carr_load_routine_db_env() {
  local env_file="${CARR_ROUTINE_DB_ENV_FILE:-$HOME/.config/carr/db.env}"
  local line key value wanted mode
  local -A allowed seen
  for wanted in "$@"; do allowed[$wanted]=1; done
  [ -f "$env_file" ] || return 0
  mode="$(carr_credential_file_mode "$env_file" || true)"
  [[ "$mode" == <-> ]] || { print -ru2 -- "cannot determine routine credential file permissions"; return 78; }
  (( (8#$mode & 077) == 0 )) || { print -ru2 -- "routine credential file must be 0600"; return 78; }
  while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    [[ "$line" =~ '^([A-Z][A-Z0-9_]*)=(.*)$' ]] || {
      print -ru2 -- "routine credential config is malformed"
      return 78
    }
    key="${match[1]}"; value="${match[2]}"
    [[ -n "${allowed[$key]:-}" ]] || continue
    [[ -z "${seen[$key]:-}" ]] || {
      print -ru2 -- "routine credential config repeats $key"
      return 78
    }
    # db.env uses quoted literal URIs; remove one matching outer pair only.
    # Never evaluate the contents: ampersands are URI query separators, while
    # substitutions/backticks remain executable-shell syntax and are refused.
    if [[ "$value" == \'*\' ]]; then value="${value#\'}"; value="${value%\'}"
    elif [[ "$value" == \"*\" ]]; then value="${value#\"}"; value="${value%\"}"; fi
    [[ "$value" != *'$('* && "$value" != *'`'* ]] || {
      print -ru2 -- "routine credential config has unsafe value for $key"; return 78; }
    [[ -n "$value" ]] || {
      print -ru2 -- "routine credential config has unsafe value for $key"
      return 78
    }
    typeset -gx "$key=$value"
    seen[$key]=1
  done < "$env_file"
}

# carr_require_sourceable_db_env CALLER [FILE]
# Refuse to let a caller `source` a credential file the shell cannot parse.
#
# THIS IS FOR THE CALLERS THAT STILL DO `set -a; . db.env`.  They do not want
# carr_load_routine_db_env above — that one is deliberately narrow and loads only
# the keys a routine names, which is the right shape for an unattended job and
# the wrong shape for a script that needs whatever happens to be in the file.
# What those callers DO need is the failure to be legible.
#
# On 2026-08-20 CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL was written into
# db.env unquoted, from outside tools/rotate-credential.py and its quoting.  Its
# `&channel_binding=require` is a background operator to zsh, so the source died
# on a parse error and took all five keys in the file with it.  Under `set -eu`
# that aborts the caller at rc=126 before it can log anything.  All a session saw
# was `.../db.env:10: parse error near `&'` — text that names neither the script
# it killed nor what that file is for, so bin/migrate-prod.sh, the one sanctioned
# door to production migrations, read as a broken script.
#
# It read as INTERMITTENT too: every one of those callers guards the source on
# NEON_API_KEY being unset, so a caller that already had it exported skipped the
# file and worked.  Aug 26 has both clean applies and a session stuck at the
# source line.
#
# ONE COPY, because of the rule that a manual path and an automated path doing
# the same job must be the same code (a8c55a47) — seven inlined copies of this
# is six chances for the wording to drift.  That rule was itself taught off this
# very file: db.env is named in its body as the config with two parsers and two
# opposite quoting contracts, which cost two days of silent outage in August.
#
# `zsh -n` parses and runs NOTHING: no connection, no expansion, no value
# printed.  The probe output is captured and never echoed — zsh quotes a token
# from the offending line and every value in that file is a credential — so only
# the line number is reported.  78 is EX_CONFIG: ran, found no usable credential,
# changed nothing, said so.
carr_require_sourceable_db_env() {
  local caller="$1" env_file="${2:-$HOME/.config/carr/db.env}" probe line
  [ -f "$env_file" ] || return 0
  probe="$(zsh -n "$env_file" 2>&1)" && return 0
  line="$(print -r -- "$probe" | sed -n '1s/^[^:]*:\([0-9][0-9]*\):.*/\1/p')"
  print -ru2 -- "$caller: STOPPED. Nothing was read and nothing was changed."
  print -ru2 -- "$caller: $env_file — the credential file this script sources —"
  print -ru2 -- "$caller: does not parse as shell at line ${line:-?}. zsh aborts the whole file"
  print -ru2 -- "$caller: at the first bad line, so EVERY key in it is lost, not just that one."
  print -ru2 -- "$caller: Almost always an unquoted connection string: the '&' before"
  print -ru2 -- "$caller: channel_binding is a background operator to the shell."
  print -ru2 -- "$caller: Fix: single-quote the whole value, as the other keys are."
  print -ru2 -- "$caller: Then: zsh -n $env_file   (silence means fixed)"
  return 78
}

# Runs a routine child with only the declared database capabilities.  Other
# credentials must be loaded by that child's own narrow adapter, never inherited
# from a broad db.env source.
carr_routine_exec() {
  local -a routine_env
  routine_env=(HOME="$HOME" PATH="$PATH" LANG="${LANG:-C}" TMPDIR="${TMPDIR:-/tmp}" \
    CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-}" \
    CARR_EXPORT_LIVE="${CARR_EXPORT_LIVE:-}" \
    HC_EXPORTS_RC="${HC_EXPORTS_RC:-}" \
    HC_BACKUP_RC="${HC_BACKUP_RC:-}" \
    HC_CHAIN_RC="${HC_CHAIN_RC:-}" \
    CARR_DB_JOBS_URL="${CARR_DB_JOBS_URL:-}" \
    CARR_DB_EXPORTER_URL="${CARR_DB_EXPORTER_URL:-}")
  # A Drive root is not routine ambient state.  It crosses this boundary only
  # after the caller has explicitly opened its recovery envelope.
  if [ "${CARR_DRIVE_RECOVERY:-0}" = "1" ]; then
    routine_env+=(CARR_VAULT="${CARR_VAULT:-}")
  fi
  env -i "${routine_env[@]}" "$@"
}
