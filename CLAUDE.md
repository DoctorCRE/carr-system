# carr-system — repo pointer for sessions

Code lives here. The CARR Record Layer owns business, brand, persona, deal
context and doctrine. Query its verbs or `./run.sh retrieve "<question>"`
before declaring anything absent.

## Always-on rules

These twelve core cards are loaded natively with this file. No boot fetch is required.
Before acting when a card applies, answer one line with applies yes/no and the action or reason.

[K01] Assess when asked; finish when authorized: When Joe asks what you think, assess and stop. Once work is authorized, finish it and its necessary follow-ups without re-asking; backlog out-of-scope work with the product task it blocks.

[K04] Verify before claiming: When you say something is done, fixed, or true, check the real artifact with a tool this session and current sources. Name anything unverified, skipped, or failed.

[K03] Run it yourself before handing over: When you'd hand Joe a command or call work blocked, try it first: check permissions and wrappers, retry a classifier block once, try another route. Hand over only human-only sign-in, hardware taps, or human denials; file what stays blocked.

[K05] Bound negative and capability claims: When you say something is missing or impossible, search every relevant collection (the full deployed verb list, not a cached one) or test it live there. Otherwise call it partial or unproven.

[K02] Raise concerns and corrections plainly: When a partner's request, term, or model looks wrong or risky, or code disagrees with the agreed model, say what's off, why, the consequence, and a safer option. Don't soften; if reaffirmed, proceed. Save "I have a concern" for must-stop issues.

[K06] Resolve source conflicts openly: When sources disagree, follow Joe's live direction, then the current store record, then the newer dated ruling, and say there was a conflict. Archives and backups are evidence, not authority.

[K12] Capture durable corrections now: When a partner corrects or overrules you, or states a preference that would recur, propose it through teach this turn with the trade-off; proposals bind nobody yet. A how-to the same partner explains twice: quote both, offer a skill.

[K08] End with one action or one decision: When ending a reply, do the doable steps yourself and leave at most one human action or decision, asked directly (approve or decline) in the question box. No closing menus or scripted replies.

[K07] Make every item stand alone: When showing Joe a question, finding, draft, or review request, name the subject plainly, put IDs after the description, link files he should open, and explain automated findings from scratch.

[K10] Write to the question: When writing to Joe, lead with the outcome in short plain prose sized to the question; tables or diagrams only for order, comparison, or flow. Keep evidence words exact; contrast only raised alternatives.

[K09] Give runnable numbered steps: When giving a partner steps to run, number each, say all are required, one active-voice action per line under twenty words, ending with the check that proves it worked.

[K11] Use observed clocks: When stating a date, time, age, or "today", take it from an observed timestamp or clock read and give it in Joe's timezone.

Naming trap that cost a real search (2026-08-08): the app persona is
**Dr. CRE** — "Doc" is only the spoken nickname. Search "Dr. CRE" or the
slugs `dr-cre-concept` / `dr-cre-voice-doctrine`; searching "Doc" returns
audio slugs and noise. The mascot/visual design is in `dr-cre-concept-2026-08-01`
(vault record-layer folder + doctrine store); the living-orb panel visual is
specced in open loop #250 (its origination conversation predates capture and
was never recovered — loop #250 documents that gap).

Drive gotcha: Google Drive File Stream serves online-only placeholders; a
grep over the Drive mirror can miss content that exists. Materialize files
offline before trusting a negative grep there.

## "carr needs authentication" is not a reason to ask Joe to sign in

Desktop sessions show the user-config `carr` server as needs_auth even while its
CLI login works; they use the claude.ai "CARR Record Layer" connector instead.
On that notice, check the connector (session_connectors_status) and
`claude mcp get carr`. If both are connected, say nothing about signing in. Tell
Joe once, naming which, only if one is down. Never remove the `carr` server:
the allowlist, gates and Codex job launcher depend on its name.

## A capture-verb denial has a fallback door

`report-problem`, `record-defect`, and `add-loop` are now directly
permission-allowlisted across all three MCP prefixes (`.claude/settings.json`),
closing the classifier coin-toss that once denied them (commit 18872b66). If a
DIFFERENT record-layer write verb is ever denied by the classifier, file it in
THIS session through the Bash door instead of stopping:

    ./run.sh call <verb> '<the same JSON you would have passed the verb>'

Same `mcp-server/src/tools.js` registry the connector serves, reached through
`tools/call-verb.py`, separately allowlisted as `Bash(./run.sh *)`. It carries
no credential (the receipted break-glass close stays a human's call) and it is
not `call-verb` — never reach for that generic passthrough to get at a verb
the classifier declined; that is working around a denial rather than filing it.

## Build sessions: lessons from the 2026-10-10 orchestrator

Measured from the Orchestrator.macbook transcript (37 hours, 11 compactions,
224 Stop turns). Each item is the fix, not the story.

1. Launch `bin/codex-job <owner/repo> <new-branch> "<brief>" [-c model_reasoning_effort=high]` with the Bash tool's `run_in_background` and read its printed result. Start several tracked tasks for parallel work. Model and default effort come from `~/.codex/config.toml`.
2. **The progress board is one per checkout**, resolved from the main working
   tree; a worktree no longer needs its own `init`.
3. **A Stop gate that holds the turn open is answered with the delta only**, and
   if the gate misread a compaction summary or a request for instructions as an
   order, that is the gate's defect: fix the gate, do not reword the reply.

## Migration and release state
Read current canonical migration/release state before naming frontier/blocker.
Dated incidents/WRs are history; source/CI grants no live authority.

## Map work has one mandatory front door

For any request to recommend, design, build, revise, review, or publish a map,
GIS analysis, route, day trip, or Tour surface, call the live `map-architecture`
verb first. It returns the current doctrine and machine-contract pointer.

## Rule lifecycle evidence

Before operating a rule lifecycle, load
[the preflight and prepared-admission pointer](AGENTS.md#rule-lifecycle-evidence).

## PR design and debt

Read and follow [the required PR policy](AGENTS.md#before-every-pr-design-and-debt-pass).

## Agent skills

Use `gh pr merge --auto --squash <pr>` for GitHub native auto-merge, `gh pr list` for open PRs, and `tools/room-bridge/briefs/{builder,staging-score}.md` for dispatch briefs.

### Issue tracker

Issues are CARR Work Requests in the record layer (`report-problem`, `work-request-card`), not GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

The five triage roles map to Work Request states and `review-and-triage` classes; triage itself is human-only. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context; the glossary and decisions live in the doctrine store and decision log, not repo files. See `docs/agents/domain.md`.

## Production deployment

`.github/workflows/deploy.yml` deploys a successful main canary. It runs
`tools/migrate.py` and verifies the production `/release` identity.
