#!/bin/zsh
# carr-system run entry point (phase 2, 2026-07-24).
# The ONE command SOPs call on Joe's Mac. Normal paths use canonical records;
# Drive-backed legacy inputs are available only through explicit recovery.
#   ./run.sh deal-room     — rebuild the Deal Room HTML from the deal record (--files for the json)
#   ./run.sh lead-board    — rebuild the canonical record-backed Lead Board HTML
#   ./run.sh renewal-feed  — recovery-only MLS feed rebuild (normal mode refuses)
#   ./run.sh all           — rebuild the canonical Lead Board, then the Deal Room
#   ./run.sh review-queue  — rebuild the ONE review queue (out/review-queue/), a surface only
#   ./run.sh brief-pack    — the four brief sections as callable units (out/brief-pack/)
#   ./run.sh restore-rehearse — prove the encrypted backups actually restore (needs the age key)
#   ./run.sh key-recovery-test — prove the OFFLINE PAPER key copy alone can recover a backup
#   ./run.sh call-mode     — inspect or operate the local Quill Call Mode bridge
#   ./run.sh local-db-ci   — run migration or strict CI on disposable local PG
# Drive-backed legacy inputs are recovery-only. Scoped commands parse their own
# explicit --recovery --reason WHY [--vault PATH] boundary and erase ambient
# CARR_VAULT in normal mode.

set -eu
REPO="$(cd "$(dirname "$0")" && pwd)"

# ORDER 29a: the Deal Room and the graph pipeline read the RECORD by default
# (v_export_* through lib/record_sources.py), so they run on the repo venv — that
# is the interpreter with psycopg. Both still accept the file path and both fall
# back to the generated files, loudly, on any machine without the credential.
# Pass --files to either one to force the old read path.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

deal_room()    { "$PY" "$REPO/generators/build-deal-room.py" "$@"; }
# The default Lead Board path is canonical records (including candidate_pool's
# renewal-radar rows). It needs the repo venv because it reads through psycopg;
# normal mode fails closed rather than falling back to Drive.
lead_board()   { "$PY" "$REPO/generators/build-lead-board.py" "$@"; }
lead_promote() { "$PY" "$REPO/pipelines/lead-promote.py" "$@"; }  # ORDER 29b flip: venv, records-mode reads (parity byte-identical 2026-08-05)
renewal_feed() { "$PY" "$REPO/generators/build-renewal-feed.py" "$@"; }  # external MLS ingress: explicit recovery only until canonical intake exists
corroborate()  { "$PY" "$REPO/pipelines/radar/corroborate.py" "$@"; }     # upstream file pools: explicit recovery only until canonical intake exists
space_search() { python3 "$REPO/pipelines/build-space-search.py" "$2"; }
graph()        { "$PY" "$REPO/pipelines/build-graph-notes.py" "$@" \
                 && "$PY" "$REPO/pipelines/build-graph-structure.py" "$@"; }
# NOTE: build-graph-notes.py wipes and rebuilds Graph/, which deletes Graph/hubs.
# The hub pass MUST run after it, so `graph` always runs both.
# phase1 (2026-08-13): both now read the doctrine store (lib/record_sources,
# carr_exporter credential) for store-held content, so both need the repo
# venv's psycopg — same ORDER 29a reasoning as the other DB-touching pipelines
# below. Plain python3 still runs them (fail-soft: file-walk everything, no
# store pass) but on this Mac it has no psycopg at all, so it would silently
# never take the store path — always prefer $PY here.
graph_system() { "$PY" "$REPO/pipelines/build-system-graph.py" "$@"; }
graph_health() { "$PY" "$REPO/pipelines/graph-health.py" "$@"; }
sf_diff()      { "$PY" "$REPO/pipelines/diff-salesforce-deals.py" "$REPO" "$@"; }  # records mode; no wrapper Drive root
section_index(){ "$PY" "$REPO/pipelines/build-section-index.py" "$@"; }
registry_audit(){ shift; "$PY" "$REPO/tools/registry-audit.py" "$@"; }
# ORDER 16. Both are READ-ONLY surfaces: no database write, no send, no push.
# They run on the repo venv because they speak to Neon through psycopg.
review_queue() { shift; "$REPO/.venv/bin/python" "$REPO/pipelines/review_queue.py" "$@"; }
brief_pack()   { shift; "$REPO/.venv/bin/python" "$REPO/pipelines/brief_pack.py" "$@"; }
verify_emails(){ python3 "$REPO/tools/verify-emails.py" "$@"; }
# The restore rehearsal (2026-08-02). Proves backups/*.sql.age can come BACK:
# throwaway Neon branch, decrypt, load, reconcile row counts, delete the branch.
# It writes NOTHING to production and needs the age PRIVATE key, which this repo
# does not carry — pass --identity or set CARR_AGE_IDENTITY. --preflight runs the
# checks alone and creates nothing.
restore_rehearse(){ shift; "$REPO/bin/restore-rehearse.sh" "$@"; }
# The key-recovery test (Program 4). Proves the OFFLINE PAPER copy of the age
# private key — never the stored ~/.config/carr/age-key.txt — can actually
# recover a backup: types the paper key (silent, never echoed), derives its
# public key and compares it to backups-public-key.txt (decisive: a mismatch
# means the paper copy is wrong and nothing else runs), then on a match hands
# the typed identity's PATH to the unmodified restore rehearsal above. One
# command; the key never touches a transcript, a log, an argv, or the repo.
key_recovery_test(){ "$REPO/bin/key-recovery-test.sh" "$@"; }
# `call` (2026-08-08): fire ONE record verb from a terminal. Wraps
# mcp-server/local-verb.mjs, which runs the REAL src/tools.js registry, and feeds
# it the DSN from tools/db-tap.py so a human does not have to produce one by
# hand. Production is reachable for reads AND writes (Joe's ruling 2026-08-09,
# loop #258): a production write prints a warning naming the verb and the host,
# and is not blocked. Use --branch to rehearse against a Neon branch instead.
call_verb()    { shift; python3 "$REPO/tools/call-verb.py" "$@"; }

case "${1:-}" in
  deal-room)    shift; deal_room "$@" ;;
  lead-board)   shift; lead_board "$@" ;;
  lead-promote) shift; lead_promote "$@" ;;
  renewal-feed) shift; renewal_feed "$@" ;;
  all)          shift; lead_board "$@"; deal_room "$@" ;;
  corroborate)  shift; corroborate "$@" ;;
  space-search) space_search "$@" ;;
  graph)        shift; graph "$@" ;;
  graph-system) shift; graph_system "$@" ;;
  graph-health) shift; graph_health "$@" ;;
  salesforce-diff) sf_diff "$@" ;;
  section-index) shift; section_index "$@" ;;
  registry-audit) registry_audit "$@" ;;
  review-queue) review_queue "$@" ;;
  brief-pack)   brief_pack "$@" ;;
  verify-emails) shift; verify_emails "$@" ;;
  restore-rehearse) restore_rehearse "$@" ;;
  key-recovery-test) shift; key_recovery_test "$@" ;;
  call)         call_verb "$@" ;;
  call-mode)    shift; python3 "$REPO/tools/dictation-rig/bin/call-mode.py" "$@" ;;
  # Normal retrieval is canonical-store only and fail-closed. The reader itself
  # resolves CARR_VAULT only after an operator passes --recovery; do not export a
  # Drive path into the ordinary user-session command.
  retrieve)     shift; "$PY" "$REPO/tools/retrieve.py" "$@" ;;
  # health runs on the repo venv for the same reason retrieve does: its ops-record
  # pass needs psycopg. Under a bare python3 it died mid-report, and the rows it
  # never reached were the nightly chain result and rules live — the two the
  # session-start brief tells sessions to go read when it reports a failure.
  health)       shift; "$PY" "$REPO/tools/health-check.py" "$@" ;;
  costs)        shift; PYTHONPATH="$REPO/tools${PYTHONPATH:+:$PYTHONPATH}" "$PY" -c 'import system_costs,sys; sys.exit(system_costs.main(sys.argv[1:]))' "$@" ;;
  config)       shift; python3 "$REPO/ops/config-as-code.py" "$@" ;;
  lint)         shift; python3 "$REPO/tools/writing-lint.py" "$@" ;;
  migrate)      shift; "$REPO/.venv/bin/python" "$REPO/tools/migrate.py" "$@" ;;
  export)       shift; "$REPO/.venv/bin/python" -m exporters.run_exports "$@" ;;
  check)        shift; "$REPO/tools/check.sh" "$@" ;;
  smoke)        shift; "$REPO/tools/smoke.sh" "$@" ;;
  # `next` (2026-08-09, loop #297): open loops ordered by how LITTLE is left,
  # which no other surface answers — bell/dated/decision all order by urgency.
  # Read-only over v_loop_proximity (migration 0084). Prints its own coverage
  # first, because most of the backlog predates the blocker gate and a ranking
  # that hid that would look authoritative and be noise.
  next)         shift; "$REPO/.venv/bin/python" "$REPO/tools/db-tap.py" run "$REPO/tools/closest-first.py" "$@" ;;
  # `next-migration` (2026-08-13): the next free migration number, read from
  # origin/main AND every linked worktree's migrations/ — not from `ls` in
  # whichever tree you happen to be standing in, which cannot see a number a
  # concurrent session has already taken. Read-only. See tools/next-migration.py.
  next-migration) shift; python3 "$REPO/tools/next-migration.py" "$@" ;;
  # `reserve-migration` (2026-08-24, ci-failures council cluster B): atomically
  # CLAIM the next free migration number at mint time. next-migration only
  # reports; two sessions asking it in the same minute still collide. The
  # reservation ledger lives in the CANONICAL checkout's out/ — the one
  # directory every worktree on this machine shares — and next-migration counts
  # its rows as claims. See tools/reserve-migration.py.
  reserve-migration) shift; python3 "$REPO/tools/reserve-migration.py" "$@" ;;
  # The report-card rubric (loop #220, DRAFT). One entry point for both paths:
  # a human runs this, and the monthly audit runs this. Rule a8c55a47 — the
  # manual path and the automated path that do the same job must be the SAME
  # code, which under v1 they were not.
  report-card)  shift; "$PY" "$REPO/tools/report-card.py" "$@" ;;
  # `worktree` (2026-08-10): an isolated checkout in one command. Two sessions
  # collided in the shared tree twice in one morning, and git-writer-gate's own
  # deny message already prescribes the fix — this makes it one step instead of
  # three, two of which (the .venv and out/ symlinks) are invisible until
  # something breaks on a path that is simply not there.
  worktree)     shift; "$REPO/bin/worktree.sh" "$@" ;;
  local-db-ci)  shift; "$PY" "$REPO/ops/local-pg-ci.py" "$@" ;;
  *) echo "usage: run.sh deal-room|lead-board|lead-promote [--count N] [--county X] [--segment X]|renewal-feed|all|corroborate|graph|graph-system|graph-health [--verbose]|section-index [each supports --recovery --reason WHY [--vault PATH]]|space-search <folder>|salesforce-diff [--apply]|registry-audit [--verbose] [--recovery --reason WHY [--vault PATH]]|review-queue [--fixture f.json]|brief-pack [--section all|one-thing|prebriefs|capacity|monday-agenda|renewal-shortlist] [--quiet] [--recovery [--vault PATH]]|verify-emails [--source registry|vendors|roster] [--segment X] [--out f.csv]|retrieve [--recovery [--vault PATH]] <question>|health [--recovery --reason WHY [--vault PATH]]|config [check|pull|install] [--apply]|lint <file> [--surface email|social|proposal|web]|restore-rehearse [--preflight] [--verify-only] [--date YYYYMMDD] [--identity PATH] [--keep-branch]|key-recovery-test|migrate [--apply] [--yes]|export [--only <target>] [--bootstrap]|call [--branch <name>] <verb> '<json args>'|call-mode [serve|state|start|stop]|check [--recovery --reason WHY [--vault PATH]]|smoke [--recovery --reason WHY [--vault PATH]]|next [--all] [--domain X]|report-card [--validate|--run] [--skip-evidence] [--recovery --reason WHY [--vault PATH]]|local-db-ci [--class migration|strict] [--port N]|worktree <name> [--from B]|--list|--remove <name|path>"; exit 2 ;;
esac
