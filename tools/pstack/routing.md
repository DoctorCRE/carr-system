# pstack cross-provider roles in T3

Read the current `~/.claude/rules/pstack-models.md`. This overlay replaces only the Claude adapter's row that omits non-Claude models. Preserve the requested family. Call `orchestrator_capabilities` once before first delegation and resolve provider/model/options from that live result. Never hard-code entitlement from this document.

| pstack role slug | T3 target providerInstanceId | T3 model | target.options |
| --- | --- | --- | --- |
| gpt-6.1-sol, gpt-6.1-terra | codex | Same detected base ID | reasoningEffort |
| claude-opus-5-5-xhigh | claudeAgent | claude-opus-5-5 | effort=xhigh |
| grok-4.7-xhigh-fast | grok | grok-4.7-build-fast | reasoningEffort=xhigh |
| grok-4.7-xhigh | grok | grok-4.7 | reasoningEffort=xhigh |
| auto, inherit-parent | Parent provider | Omit explicit model | Inherit |

Parse effort separately from base model, including GPT slugs ending in `-high` or `-xhigh`. Use only options the live schema accepts. Claude fast maps to fastMode only if offered; GPT priority is not a synonym for Cursor fast. An unavailable provider/model is a reported blocked role, never a silent fallback. Grok routes are documented for restoration; do not dispatch Grok while balance is exhausted.

Use `delegate_task` with `target` and `isolation: worktree` for pstack cloud workers. Put the role wrapper, poteto-mode absolute path, Card, full brief and standing orders in `task`. Honor returned `next`. Completion wakes the parent; do not poll or use a top-level thread as a subagent. A worker starts no children unless its task explicitly authorizes them. Builders use Codex Sol for contract/design/risky work and Terra for routine work. Coordinators use Claude Opus 5.5. Verifiers use the other family from the builder. The `swarm workers` line supplies the normal verifier family; reverse it explicitly for a Claude-built unit.

Start every worker with `/pstack:poteto-mode` semantics by reading the installed SKILL.md in full as a poteto-agent worker. Paste `/Users/booko/.claude/orchestrate/doctorcre-delivery/preferences.md` verbatim into every remote brief and every resume. Local tasks may reference it. Keep GOAL, SCOPE, CONTEXT, ACCEPTANCE, VERIFY, TIMEBOX, FORBIDDEN, REPORT, STANDING. Read the sibling leaf skills as pstack directs.

CARR's Card registration, record-layer Work Requests and native App review gate and no-pause deployment rule e27d8b3b remain adapters around pstack. The ledger does not submit a forge review. Report unresolved process conflicts for Joe; do not quietly rewrite pstack. Rule 60587e0f governs the pstack store. It stays local as built; decisions, closed gates and a status mirror publish through record-layer verbs via `~/.claude/orchestrate/bin/publish.py`. Run `publish.py decisions` after logging decisions or closing gates, and `publish.py status` after every unit state change. Pass the owning `work_request` on those writes. Business records stay in record-layer verbs.
