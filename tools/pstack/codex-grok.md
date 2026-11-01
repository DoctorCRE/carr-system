# pstack in Codex and Grok

Read this adapter once per session. Follow pstack as shipped. The installed tree retains its Claude adapter for Claude sessions; use this mapping on Codex and Grok. Read `~/.claude/rules/pstack-models.md` and `~/.claude/rules/pstack-routing.md` before selecting roles. They are not automatically loaded by Codex.

## Tools

| Cursor | Codex | Grok |
| --- | --- | --- |
| Task | `spawn_agent` for same-provider work completed this turn; T3 `delegate_task` for cross-provider, isolated or background work | Use T3 `delegate_task`; inspect the current schema before calling |
| AskQuestion | `request_user_input` in Plan mode, `request_user_input_async` when offered, otherwise plain text | Offered question tool, otherwise plain text |
| Open a todolist | `update_plan`; otherwise a maintained list in the reply | Offered todo tool; otherwise a maintained list in the reply |
| Run another pstack skill | Read its sibling `<name>/SKILL.md` in full and follow it | Same |
| Grep, Glob | `exec_command` with `rg` | Shell with `rg` |

## Subagent fields

Native Codex accepts `task_name`, `message`, `fork_turns`, and supported `model` and `reasoning_effort` overrides. Put `poteto-agent` or `comment-sicko` instructions from the installed `agents/` file in the message; there is no native `subagent_type`. Include the absolute poteto-mode SKILL.md path. `generalPurpose` means the default agent. `readonly` is an explicit no-edits instruction, checked with git status afterward. `environment: cloud` uses T3 `isolation: worktree`; pass `cloud_base_branch` as the branch/commit in the brief. `environment: local` needs no field. `file_attachments` become absolute file pointers. `resume` means a fresh task with the consolidated earlier result. Native background fields are omitted; await completion in this turn. For background work use T3 and its completion notification, never poll. Drop every unsupported field and state what was dropped.

## Models

Native Codex uses detected `gpt-*` model IDs and `reasoning_effort`, without Cursor effort or fast suffixes. `auto` and `inherit-parent` omit the model. A Claude or Grok role goes through the T3 routing overlay; never silently replace it with the parent family. Outside T3, report that cross-provider role unavailable. Grok calls are suspended while its balance is exhausted. Setup uses the live T3 model catalog and its option values, then writes pstack's exact role labels to `~/.claude/rules/pstack-models.md`. Never put role lines in AGENTS.md.

## Paths

The updater installs a complete tree at `~/.agents/skills/pstack/`, with skills under `skills/`, agents under `agents/`, and docs under `docs/`. Relative bundled leaf links retain their original layout. Codex discovers user skills recursively under `~/.agents/skills`; legacy `~/.codex/skills` can coexist, but this installer writes only the former. Invoke `$poteto-mode` in Codex; `/pstack:poteto-mode` in Claude. In Grok select the discovered `poteto-mode` skill or read its absolute path. Resolve upstream GitHub pstack file links to the installed local copy when present. Transcripts use the actual session's reported path, not Cursor's transcript directory. `.cursor/rules` maps to the host's guidance entrypoint; `.cursor/skills` maps to the permitted user skill tree when project writes are forbidden.

## Cursor-only features

Cursor Automations and cloud dashboards have no portable equivalent. Skip their steps with one explicit reason and continue. Bugbot comments are ordinary forge review threads; only skip starting or configuring an absent Bugbot. Use `gh` for the built-in PR tool. Never invent a URL or secret for a skipped step. Cursor built-ins and cursor-team-kit skills are separate dependencies; report unavailable ones rather than fabricate substitutes.

## Update

Run `python3 tools/pstack/update.py` from this checkout. It reads the upstream generated Claude tree without editing the marketplace or cache, wraps only SKILL.md entrypoints with this adapter pointer, and reconciles only files it owns. It refuses unowned collisions or locally edited managed files. Rerun after updating upstream through its own plugin lifecycle. The manifest records the upstream revision and source hashes. It does not fetch or rebuild upstream.
