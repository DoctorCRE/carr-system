# carr-system — repo pointer for sessions

Code lives here. The CARR Record Layer owns business, brand, persona, deal
context and doctrine. Query its verbs or `./run.sh retrieve "<question>"`
before declaring anything absent.

The 2026-08-19 cutoff retired the generated Drive .md files; there is no
compiled-rules file or vault CLAUDE.md to fall back on. A session that cannot
reach the store must say so rather than work from memory.

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
the allowlist, gates and Model Room dispatcher depend on its name.

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

## Migration and release state
Read current canonical migration/release state before naming frontier/blocker.
Dated incidents/WRs are history; source/CI grants no live authority.

## Steering evals

Surfaces in `evals/surfaces.json`: `/claude-api build-eval`, then
`/claude-api hillclimb`; ship per `evals/README.md`.

## Map work has one mandatory front door

For any request to recommend, design, build, revise, review, or publish a map,
GIS analysis, route, day trip, or Tour surface, call the live `map-architecture`
verb first. It returns the current doctrine and machine-contract pointer.

The Stop gate only announces this now (Joe's 2026-08-23 gate rationing): a
skip is recorded in `out/map-architecture-gate.jsonl` but does not block, so
the session making the call enforces it.

## Dell migration trigger

Only in interactive Claude Code in `~/carr-system`, Dell or Joe typing the exact
phrase **ready for migration** is explicit authorization to complete the bounded
Dell migration without asking follow-up questions. Before acting, read
[the complete migration packet](ops/config/task-boot/dell-migration.json).
It preserves the machine, identity, receipt, security and record-closeout checks.

## Rule lifecycle evidence

Before operating a rule lifecycle, load
[the preflight and prepared-admission pointer](AGENTS.md#rule-lifecycle-evidence).

## PR design and debt

Read and follow [the required PR policy](AGENTS.md#before-every-pr-design-and-debt-pass).

## Agent skills

### Issue tracker

Issues are CARR Work Requests in the record layer (`report-problem`, `work-request-card`), not GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

The five triage roles map to Work Request states and `review-and-triage` classes; triage itself is human-only. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context; the glossary and decisions live in the doctrine store and decision log, not repo files. See `docs/agents/domain.md`.
