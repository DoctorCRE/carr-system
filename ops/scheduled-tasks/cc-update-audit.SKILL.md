---
name: cc-update-audit
description: Weekly (Mon) Claude Code update audit: on any version change, fires the IT Support lane to audit what the release changes for CARR. Detection is no longer this task's job — the free launchd sentinel com.carr.cc-version-sentinel checks both binaries hourly and notifies Joe within the hour; this session does the judgment half only, and scopes across every release stacked since its last run.
---

You are running the CARR system's Claude Code update audit. This is an unattended scheduled run. Joe Bookout is not necessarily at the keyboard.

## STEP 0 — THE GATE. Do this first, before reading anything else.

Run exactly this:

**There are TWO Claude Code binaries on this Mac and they are usually on different versions.** The PATH binary (`/opt/homebrew/bin/claude`, npm-global, self-updating) is what `claude --version` reports. The desktop app ships and updates its OWN runtime under `~/Library/Application Support/Claude/claude-code/<version>/`, and THAT is what actually executes Joe's sessions. On 2026-08-09 they were 2.1.226 and 2.1.222 respectively. Watching only the PATH binary blinds this audit to the runtime that matters, so the gate tracks both and fires when EITHER moves.

Run exactly this:

```
SENTINEL=~/.claude/scheduled-tasks/cc-update-audit/last-audited-version.txt
CUR="$(cd ~/carr-system && ./bin/cc-version-string.sh)"
LAST=$(cat "$SENTINEL" 2>/dev/null || echo "none")
echo "current:      $CUR"
echo "last_audited: $LAST"
```

**If that command cannot run at all** (missing `~/carr-system`, the `cd` fails, the helper isn't there), do NOT treat that as "no change" — a broken gate must never silently skip the audit. Say so in one line and treat it as PROCEED: continue to Step 1 as if the strings differed.

**If `current` equals `last_audited` exactly, STOP IMMEDIATELY.** Reply with one line: "No Claude Code update since $LAST. No audit needed." Do nothing else. Do not read files, do not spawn agents, do not call verbs. This is the normal outcome on most days and it must cost almost nothing.

**Only if the strings differ** do you continue to Step 1. Say in your report WHICH binary moved, because it changes what the findings mean: a change in `app=` is present tense and affects Joe's sessions now, while a change in `cli=` is only what headless or PATH-invoked work would pick up.

Every version between the old and new numbers is in scope, not just the newest, because weekends and closed-app days stack several releases. When the two binaries sit at different versions, scope runs from the LOWER of the two audited numbers to the HIGHER of the two current ones, so nothing falls in the gap between them.

## STEP 1 — Get the actual release notes

Fetch the changelog and extract every entry from the version AFTER `last_audited` through `current` inclusive:

https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md

Use WebFetch. If that fails, fall back to `gh api repos/anthropics/claude-code/contents/CHANGELOG.md --jq '.content' | base64 -d`. If you cannot get the real notes from a primary source, STOP and report that you could not, then leave the sentinel unchanged so the next run retries. Never audit from memory or from a secondhand summary. A claim about what a release does must come from the release notes themselves.

## STEP 2 — Delegate to the IT Support lane

This is IT Support's job, not the main session's. Spawn the `it-support` agent with the Agent tool. Hand it the extracted changelog entries verbatim in the prompt, along with the version range, and ask it to answer one question:

**"What in this release changes what the CARR system can or should do, and what in this release breaks or de-risks something we already run?"**

Direct it to assess against the system as it actually exists, specifically:
- The scheduled tasks in ~/.claude/scheduled-tasks/ (16 of them, several headless), especially anything touching MCP connection, print mode / `-p`, permissions, sandboxing, or background sessions
- The record layer and MCP server in ~/carr-system (the ONE code repo; if it cannot reach that repo it must say so and stop, never improvise another home)
- The skills in {{HOME}}/My Drive/.claude/skills/ and the agents in {{HOME}}/My Drive/.claude/agents/
- The browser lanes: costar-operator, salesforce-reader, and any Chrome-driven SOP
- Cost and token behavior, which gates subagent use

It fixes NOTHING. Every finding must arrive with the remedy command or the exact file edit pre-written, for Joe or a later session to execute. That is the IT Support contract and it holds here.

Also require it to separate findings into three buckets and to say plainly when a bucket is empty:
1. **New capability we should adopt** — something the release makes possible that the system wants
2. **Now-redundant** — an instruction, workaround, or SOP step the release makes unnecessary
3. **Risk or regression** — something the release changes that could break a run we depend on

Anything platform-gated (Linux/WSL-only, VSCode-only, Windows-only) must be marked NOT APPLICABLE, since Joe runs macOS in the Claude desktop app. Do not present an unusable feature as an opportunity.

## STEP 3 — File the actionable items, do not write a report file

Do NOT create a markdown report. Findings go into the record layer, never into a per-session file.

For each finding that has a real action behind it, call `add-loop` with a clear title, the version that produced it, and the pre-written remedy in the body. Use a fresh UUID idempotency_key per call. If a finding is informational only, it does not get a loop.

If the record layer is unreachable, do not drop the findings: say so explicitly at the top of your reply and include the full `add-loop` calls pre-written so Joe can fire them.

## STEP 4 — Close the sentinel

Only after Steps 1 through 3 have actually completed, write BOTH audited versions to the sentinel so the next run does not repeat this audit. The format must match Step 0's `CUR` string exactly, or the gate compares unlike strings and re-audits forever:

```
CLI=$(claude --version 2>/dev/null | awk '{print $1}')
APP=$(ls -1 ~/Library/Application\ Support/Claude/claude-code/ 2>/dev/null | sort -V | tail -1)
echo "cli=${CLI:-none} app=${APP:-none}" > ~/.claude/scheduled-tasks/cc-update-audit/last-audited-version.txt
```

If the audit failed partway, leave the sentinel alone.

## STEP 5 — Report to Joe

Keep it short and scannable. Joe's standing preference is concise, plainly worded, no padding. Lead with one line: the version range audited and how many loops you filed. Then the three buckets, a few lines each, and nothing else. Name what is NOT applicable rather than silently omitting it. No em-dashes.