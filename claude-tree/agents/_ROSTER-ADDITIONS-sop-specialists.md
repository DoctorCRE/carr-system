# Roster additions: the SOP specialist tier (merge into README.md)

*Written 2026-08-02 by the build session that created the four files. This is staging, not a second
roster. Merge the sections below into `README.md` and delete this file. Nothing here edits README
directly, because another session was writing it at the same moment.*

---

## Section to add to README.md, after "The census: seven chairs, and why not fifteen"

### The second tier: SOP specialists

The seven chairs are **lenses**. They read a document and argue with it, and they write nothing by
construction.

The four files below are a different animal: **operators**. Each one owns a single standing
procedure end to end, carries that procedure in full inside its own file, and in two cases writes to
the record. They are not chairs, they never sit on a panel, and they are not counted in the
seven-chair census.

**Joe's design thesis, 2026-08-02, and the reason this tier exists:** *"An agent who has one job
would never forget their simple list of rules and procedures. All you need to know is who is the
agent for the job."* It is load-bearing and it was proven the same day: the main session misread a
database column while holding an entire system audit in context. A specialist holding one procedure
does not make that mistake. So each file carries its ENTIRE procedure and every hard rule for its
job, verbatim, in the file. The SOP stays the source of truth for depth. The agent file carries the
non-negotiables. Nothing critical is written as "see the SOP."

| # | Specialist | File | Source SOP | Job |
|---|-----------|------|-----------|-----|
| 1 | CoStar operator | `costar-operator.md` | `DNA/Leads/costar-playbook.md` + `DNA/Deal Management/space-search-sop.md` | Drives the paid CoStar subscription and pulls exports |
| 2 | Salesforce reader | `salesforce-reader.md` | `DNA/Deal Management/salesforce-read-sop.md` | Captures the deal report out of Salesforce and reconciles it |
| 3 | Client intake | `client-intake.md` | `DNA/Clients/intake/intake-template.md` + `intake/README.md` | Runs the intake interview and lands it in the record |
| 4 | Benefit summary | `benefit-summary.md` | `DNA/Deal Management/benefit-summary/benefit-summary-sop.md` + `playbooks/deal-post-mortem-template.md` | Caps a won deal: client scoreboard plus internal post-mortem |

**Placement test, per `DNA/Team/skills-rule.md`.** These are agents, not skills and not new SOPs.
The procedure already exists as a shared DNA file in every case, and it stays there as the single
source of truth; what did not exist was a seat that reliably holds the whole procedure in context
while executing it. An agent is the mechanism for that, and it does not fork the doctrine the way a
per-partner skill copy would. No new SOP was created and none was edited.

### Tool grants, and why each one is shaped the way it is

The chairs are all `tools: Read, Grep, Glob`, which makes write-nothing structural. These four are
deliberately different, and nesting was confirmed on 2026-08-02: an agent CAN spawn a sub-agent, and
spawning is gated by `subagent_type`, not by depth. So a broad grant is a live decision.

**Standing constraint: an agent that can spawn does not also carry write verbs.** All four are held
to it, and in the frontmatter rather than in prose. `disallowedTools: Agent` is a real Claude Code
frontmatter field and it denies spawning while leaving everything else inherited.

| Specialist | Grant | Denied | Why |
|---|---|---|---|
| `costar-operator` | `Read, Grep, Glob, Bash, mcp__Claude_Browser` | `Agent`, `mcp__claude-in-chrome`, `Write`, `Edit` | Chrome is denied **structurally**, not by prose, because a Chrome click is the one failure mode that costs Joe his subscription. The riskiest agent does not also get to be the most powerful one: no write verbs, no spawning. Bash exists only to move the export into `source-exports/`. |
| `salesforce-reader` | `Read, Grep, Glob, Bash, Write, mcp__claude-in-chrome` | `Agent`, `mcp__Claude_Browser` | Chrome is where Joe's Salesforce session lives and the capture script needs the javascript tool. The desktop browser is denied so the two platform surfaces never blur. `Write` is for one file, the TSV. No record verbs: every write this run implies is either a Joe-gated `--apply` or a confirm-this suggestion a human must rule on. |
| `client-intake` | everything inherited | `Agent` | The verbs ARE the job. An intake that produces markdown and no records has stranded the interview. Verbs are inherited rather than named because the record-layer MCP server surfaces under an install-specific prefix, and a hardcoded allowlist would silently strip them on Dell's machine or after a reinstall. |
| `benefit-summary` | everything inherited | `Agent` | Same reasoning. Also needs Bash for the node generator, the PDF render and `run.sh lint`, and Write for the terms JSON and the post-mortem file. |

**The honest caveat on the two broad grants — RESOLVED 2026-08-10, loop #231, decision fb27a42d.**
Inheriting everything also inherits connectors that can post and spend. They were held off by the
human-gate rail in prose, not by frontmatter. That is now fixed, and the reasoning below corrects an
error in the original paragraph rather than merely updating it.

The original objection was that "a mistyped entry in `disallowedTools` fails OPEN, which is the wrong
failure direction." True in isolation, and wrong as a reason to do nothing, because it compares
against a perfect control rather than against the status quo. THE FAILURE DIRECTIONS ARE NOT
SYMMETRIC:

  * A wrong or absent UUID in `tools:` (an allowlist) SILENTLY STRIPS the record verbs. The agent
    still launches — Claude Code only refuses to launch when NOTHING in the list resolves, and
    `Read` always resolves — so an intake runs, interviews, and lands nothing. Broken, quietly.
  * A wrong or absent UUID in `disallowedTools:` merely LAPSES back to the posture we already have.
    Never worse than today, anywhere.

So the denylist entries are free: strictly better on this machine, never worse on Dell's. Both files
now deny the two connectors that can act on the outside world — Blotato
(`mcp__3ccf6856-…`, which PUBLISHES) and Meta Ads (`mcp__52e52b1a-…`, which SPENDS). Gmail is
deliberately left reachable: drafting to Joe's own address IS the sanctioned handover channel (rule
9b081605), and sending follows Joe's ask (decision 41906a30).

Server-level patterns in frontmatter were confirmed against the primary source this day
(code.claude.com/docs/en/sub-agents.md): both fields accept `mcp__<server>` and `mcp__<server>__*`,
and `disallowedTools` additionally accepts `mcp__*` for every MCP server at once. What does NOT
exist is a portable way to GRANT one server without naming it, which is why the allowlist conversion
stays rejected and these two keep their broad grant.

### The five hard rails, present in full in all four files

Written into each file rather than referenced, per the thesis.

1. **Provenance inline.** Every number carries the query, command or source that produced it. A bare figure is unfalsifiable prose. This caught four wrong claims in the 2026-08-02 audit.
2. **Never assert absence from a partial search.** Check the full collection before saying something does not exist. Four independent readers made this error in one day.
3. **Stale is not wrong.** Before calling a record or a prior claim wrong, check whether something changed after it was written.
4. **Findings go to the DATABASE via verbs, never to a markdown report.** `record-finding` for OSINT and enrichment, `log-activity` and `stamp-touch` for contacts, the update verbs for fields. Doctrine and narrative stay markdown; records and findings do not. Before claiming a verb does not exist, read the full list: `grep -oE '^  "[a-z-]+": \{' ~/carr-system/mcp-server/src/tools.js`. Verbs are named for behavior, not for the column they write.
5. **Sending follows Joe's ask.** Claude sends email and other outbound messages on Joe's behalf whenever Joe asks, for one message or as a standing instruction for a category; with no ask, the default is a draft, not a gate (decision 41906a30, 2026-10-20). Every send goes through Joe's own accounts and is checked against Sent Items and logged to its record. Claude never types credentials. No account creation, no spend.

Rails 1, 2, 3 and 5 apply to the chairs too, in their own wording. Rail 4 is new with this tier,
because it is the first tier that can write.

### How to invoke a specialist

One at a time, by name. These do not run as a panel and they do not run in parallel with each other:
two of them drive browsers, and two of them write to the record.

```
Agent: costar-operator
Task: <the search, in plain language>
Client: <C-ID and name, or "market sweep, no client">
Deliverable: <export | export plus findings>
```

The calling session owns what happens next. `costar-operator` and `salesforce-reader` hand back
findings; the session lands them with verbs. `client-intake` and `benefit-summary` land their own.

### Maintenance line to add to the README maintenance section

- Specialists follow their source SOP. If a specialist file and its SOP ever disagree, the SOP wins on depth and the specialist file wins on the hard rules, which are there precisely because they must survive a session that never opens the SOP. When a rule changes, change both in the same session.
- Per the roster rule, adding a specialist updates all four documentation spots: `.claude/CLAUDE.md`, `CARR AI/INDEX.md`, `DNA/Team/skills-rule.md`, and decision-history.
- A specialist is a contract versioned to a model. All four run on Opus. If one is retiered, re-verify it on a known-good sample before trusting a live run.

---

## Also update, in README.md's existing text

- The opening line says **"Seven single-lens reviewers."** It stays true of the chairs. Add after it that the folder now also holds four SOP specialists, which are a separate tier and a separate census.
- The census paragraph's closing sentence, "Any session claiming a different census verifies against this directory before believing it," should be extended to say that the directory now holds two tiers and a count of files is not a count of chairs.
- `marketing-coo.md` is also in this directory and is neither a chair nor an SOP specialist. Whoever owns the README should say what tier it belongs to, because a reader counting files currently gets twelve and no explanation.

---

## Flagged for Joe

1. ~~**The `disallowedTools` connector gap**~~ **CLOSED 2026-08-10** (loop #231, decision fb27a42d) and it was never Joe's to rule: rule aa411351 names agent design and permissions as internal, decided and reported. Both connectors are now denied by server pattern; the failure-direction analysis is in the corrected caveat above. A denial that lapses on another machine is not a cost, because lapsing lands on exactly the posture that machine has today.
2. **Three verbs exist in the code but are NOT exposed by the connector in this session:** `record-finding`, `reassign-deal` and `retire-rule` are all present in `~/carr-system/mcp-server/src/tools.js`, and none of the three appears in the live MCP tool list. `record-finding` is named in rail 4 as the landing path for OSINT results, so if the exposed build really is behind the repo, `client-intake` cannot land its research findings and will correctly stop rather than write them to markdown. Worth checking whether the running server needs a restart or a rebuild.
