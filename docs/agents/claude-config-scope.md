# Claude Code configuration scope

CARR's Claude Code server and hooks belong to carr-system's checked-in project
configuration. Personal servers remain in user configuration. This implements
Joe's standard-scoping ruling, decision 4f9cedde-9314-4451-ac13-83df281a13ca.
The retired multi-user S3 design is not used.

`.mcp.json` preserves the HTTP server name `carr` and endpoint
`https://api.doctorcre.com/mcp`. Authentication remains Claude Code's existing
OAuth storage; no token, header or credential is copied into Git. The allowlist,
native rule-delivery recognition, and retained `mcp__carr__*` callers therefore
keep their names. Codex's independently configured MCP and hooks are unchanged.

`.claude/settings.json` enables `carr` through `enabledMcpjsonServers` and runs
hooks through `CLAUDE_PROJECT_DIR`. A checkout must have its normal `.venv` runtime.
The worktree bootstrap uses fixed `/usr/bin/python3` so it can establish that
runtime link before the runtime exists. Missing safety scripts still refuse;
missing convenience scripts still warn. A worktree runs its checked-out hooks,
whose wiring and hashes are checked by gate-integrity. Machine convergence
resolves Git's common directory so it still converges the canonical machine
checkout rather than fast-forwarding a worker branch.

## Hook inventory

The legacy user registrations are frozen in
`ops/config/claude-legacy-user-hooks.json` solely for exact removal. They are
not an installation source. Each registration moves into project settings.

| Hook | Events | Protection |
| --- | --- | --- |
| rule-pack-preuse-reselection.py | PreToolUse, Stop, UserPromptSubmit | Current rule selection and delivery |
| guard-unattended.py | PreToolUse | Outbound effects and delegated-job admission |
| record-home-gate.py | PreToolUse | Business record placement |
| orchestrator-question.py | PreToolUse | Coordinator question behavior |
| lint-gate.py | PostToolUse | Writing lint |
| scheduled-run-record.py | Stop | Durable scheduled-run receipt |
| rule-claim-gate.py | Stop | Rule-claim verification |
| orchestrator-rotation.py | Stop, PreCompact | Coordinator context rotation |
| gate-integrity.py | SessionStart | Sealed scripts and installed registration integrity |

Existing project hooks remain project-scoped. `agent-card-gate.py` admits metered
jobs. `worktree-self-plumb.py` establishes worktree runtime links.
`machine-converge.py` reconciles secondary machines. `team-memory-index.py`
loads the team-memory index. The project template now includes these hooks too.

## Consumer inventory

- `CLAUDE.md` and `.claude/settings.json` retain the name `carr`. Authentication
  diagnostics use `claude mcp get carr`; the permission allowlist retains all
  supported connector prefixes. Scope changes do not rename tools. The former
  Codex job launcher is absent from current tracked source; its stale reference
  in CLAUDE.md is removed. `ops/config-as-code.py` still manages native Codex
  configuration independently, including its carr/carr-records recognition.
- `hooks/gate-integrity.py` reads this checkout's `.claude/settings.json`, not
  user settings or the canonical checkout's project config. It checks settings
  and `.mcp.json` hashes in addition to the hook template and script hashes.
- `ops/config-as-code.py` checks, captures and installs the project hooks block.
  It preserves other project keys and never reinstalls Claude hooks user-wide.
  `bin/migrate-dell.sh` and `hooks/machine-converge.py` use that installer.
- `bin/sync-settings.sh` has already retired normal Drive settings mirroring;
  its explicit noncanonical recovery import is not an installer.
- `hooks/session-brief.py` counts project registrations. `tools/health-check.py`
  calls the gate-integrity checker for enforcement wiring. Hook command,
  interpreter, native registration, cloud path and machine-converge tests cover
  the project source rather than requiring a user-wide registration.
- `lib/headless_tasks.py` launches Claude with `cwd=repo`, including authentication
  status, and explicitly reads project permission settings. Its LaunchAgent uses
  the repo argument. Other inspected launchd jobs run Python or shell programs,
  not Claude Code sessions; their working directories do not give them hooks.
- The observed native Desktop scheduler snapshot has five enabled tasks, all
  starting in `/Users/booko/carr-system`: radar-weekly, npi-sweep-weekly,
  content-fuel-harvest-weekly, contact-enrichment-weekly, social-batch-weekly.
- T3 program workers start in platform-managed carr-system worktrees, including
  `/Users/booko/.t3/worktrees/carr-system/…`. App/software-factory jobs launched
  there retain that CARR session's project scope while editing their separate
  `/tmp` checkout. Starting a *new* Claude session directly in an app or factory
  checkout will intentionally have no CARR project configuration.
- Local inspection of doctorcre-app at `03cf3a04664f842c3ff5d01b2424cc6ba21a3dba`
  and software-factory at `f2274052257c44c2afa8083187fee7b84e3bcb50` found no
  `.mcp.json`, `.claude/settings.json`, or `.claude/settings.local.json`. Neither
  has user-wide files to move in its repository. Their sessions currently inherit
  this Mac's user settings, so direct new sessions lose CARR access as intended.
- Historical vault-rooted Claude sessions are documented in session-brief.py;
  their own project settings are independent of this user-wide move. They are
  not evidence that new sessions always start in carr-system. No live enabled
  native scheduled task inspected starts there. Arbitrary manually opened
  sessions outside carr-system lose the removed user hooks by design.
- `cua-computer-use` and `rea` are personal utility MCP servers and remain
  user-wide. The cleanup preserves all other settings, plugin registrations,
  permissions, account fields, authentication storage and modified registrations.

## Post-merge removal

Run these on each Mac from the merged carr-system checkout. The first command
is a dry run. The second removes only the exact legacy registrations with
private, uniquely named backups beside each changed file. Repeating either
command is safe. Unknown or edited hook registrations remain for inspection;
a changed `carr` server causes refusal before writing anything.

```sh
python3 ops/scope-agent-config.py
python3 ops/scope-agent-config.py --apply
claude mcp get carr
```

Restart existing Claude Code sessions after cleanup to use the new scope.
Verify `claude mcp list` in carr-system and a worktree, then in Life AI.
The Life AI project is not edited by this migration.

Upstream references:
[settings scopes](https://code.claude.com/docs/en/settings) and
[MCP scopes and approval](https://code.claude.com/docs/en/mcp).
Project approval settings apply after workspace trust. A repository cannot
approve its own servers in an untrusted folder on recent Claude Code versions;
workspace trust remains Claude Code's normal first-open requirement. Print and
SDK sessions load project servers without the interactive MCP approval prompt.

## Codex project hooks

Codex CLI 0.166.0 supports trusted project `.codex/hooks.json` layers.
[OpenAI's hook documentation](https://learn.chatgpt.com/docs/hooks) describes
additive user/project loading and per-definition trust. It also specifies that
commands run in the session cwd and recommends Git-root resolution for project
hooks. The tracked Codex commands use that mechanism for subdirectories and
linked worktrees. `/hooks` reviews and trusts new definitions; cleanup does not
silently grant trust. The old untracked shared-root hooks file is historical
evidence, not the deployed contract.

`scope-agent-config.py` now previews both clients and removes exact legacy
Codex event, matcher, and handler tuples, including their timeout and context
limit. It retains modified or unrelated hooks and backs up each changed file
with mode 0600 before `--apply` replaces it. Run cleanup after merge on both Macs.
The config installer and gate-integrity check now use the checkout's Codex file
so convergence cannot reinstall CARR hooks into user-wide configuration.

Only carr-system registers these hooks. doctorcre-app and software-factory do
not inherit CARR runtime gates. The user-level cleanup benefits all other repos
without adding registrations there. Personal pstack configuration stays user-wide.
