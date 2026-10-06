# Rule-store curation plan — the proposed backlog and the retirement of `ai-operating-notes.md`

*Written 2026-08-02 (measured 2026-08-03 UTC) by an analysis seat. NO write verbs were called; nothing in the store changed. This is a plan the main session executes with Joe. Internal document: per active rule `ede4c735`, `DNA/writing-rules.md` does not bind it.*

Joe's two rulings frame everything below:
1. `00_Context/ai-operating-notes.md` **retires**. Its content redistributes and the file goes away as each part lands. ("yes lets make them skills and retire the operating notes as we do so.")
2. **Curate first, activate in passes.** Bulk activation rejected; mass retirement rejected.

---

## 1. Measured state, re-verified

| Fact | Value | Query / source |
|---|---|---|
| Rules by status | **63 active, 54 proposed, 4 retired** | `select status, count(*) from rule group by status` |
| Bulk-ingest minute | **52 proposed created at 2026-08-01 16:19:39.447443+00**, all identical timestamp | `select date_trunc('minute',created_at), status, count(*) from rule group by 1,2` |
| Source of the 52 | `pipelines/import_operating_notes.py`, ORDER 40 step 2 | every one carries `scope.source = "ai-operating-notes"` |
| Proposed that are NOT from the file | **2** — `2e8b4840` and `305df62b` | `scope = '{}'` on both |
| Shared vs personal in the 52 | **44 shared, 8 personal (joe)** | `personal_to` join in the dump |
| `compiled-rules-shared.md` | 28 rules at last export, **35,991 bytes**, flat bullet list | `wc -c`; `exporters/targets.py:379-386` |
| `compiled-rules-joe.md` | 15 rules, 12,112 bytes | same |
| `ai-operating-notes.md` | 94 lines, 11 sections | `wc -l` |
| Active shared non-intro rules right now | **29** (28 at the 01:07:20 export + `ede4c735` activated 01:07:33) | active dump |

The brief's numbers hold, with one correction: **63 active, not 60** (three more landed during the evening).

### The importer is documented, and its own reasoning is the best evidence

`pipelines/import_operating_notes.py` carries a declared classification table (lines 82-165) plus a `PARK_REASON` map. Three facts from it that shape this whole plan:

- **It imported 52 of 67 items and PARKED 15**, never forcing a non-rule into a rule row. The parked 15 are: the seven COO-roster bullets, and eight named items (`core-conduct#13`, `standing-mechanics#3/#4/#6/#13`, `file-folder-standards#4`, `channels-routing#3`, `the-every-session-interrupt-#1`).
- **None of the 52 has a `human_quote`**, deliberately. Docstring: "`teach` REQUIRES human_quote — and these rules do not have one... Calling `teach` for these would mean fabricating 60-odd quotes into a column whose whole meaning is 'the human's literal words'." Every row carries `"quote_absent": true` and a provenance string instead.
- **Every imported row already carries `scope.section`** (e.g. `{"key": "core-conduct#4", "section": "core-conduct", ...}`). This is the single most useful thing in the whole backlog — see Pass 1.

### One premise in the brief needs correcting

The brief says *"'Working with Joe' is PERSONAL-scope content sitting in a shared-tier file."* It is the reverse. `00_Context/` is Joe-personal by definition (`CARR AI/CLAUDE.md:9`: "Everything outside DNA/ is Joe-personal and never shared"). So `ai-operating-notes.md` is a **personal file carrying mostly shared content** — the importer says so in its own docstring: "this file is Joe-personal, but ~two thirds of its content governs shared assets and lands SHARED regardless of which file it sat in." The scope error markdown could not express is that 44 of these 52 rules should have been reaching Dell's brain and never did.

---

## 2. Two blockers, named up front

### Blocker A — `retire-rule` is written but NOT deployed

- Present in code: `mcp-server/src/tools.js:2230-2266`, `write: true, humanOnly: true`, requires a `reason`.
- **Absent from the live connector's tool list.** The session's MCP roster for server `b36e17b6` lists `teach` and `activate-rule` and no `retire-rule`.
- Gate: a `wrangler deploy` of `mcp-server/`.
- **What it blocks:** every RETIRE disposition below (5 rules), and every REWRITE that wants the superseded row cleaned up. It blocks **nothing** about activation — a proposed rule binds nobody, so the activation passes can run today and the retires can trail behind the deploy.

### Blocker B — there is no verb to edit a rule

`activate-rule`'s entire input schema is `{idempotency_key, rule_id}` (`tools.js:2216-2218`). It cannot change `statement`, `scope`, `personal_to`, or `enforcement`. There is no `update-rule` anywhere in the registry.

Consequence: **every REWRITE-THEN-ACTIVATE is a fresh `teach` + a `retire-rule` on the old row** — so rewrites are deploy-gated too, unless Joe accepts leaving the wrong row sitting proposed. And `teach` **requires** `human_quote`. The clean path, which also fixes the quote-absent problem for the eight rules that most need Joe's judgement anyway: **Joe restates each rewritten rule in his own words during the sitting**, and that sentence becomes the genuine `human_quote`. The importer refused to fabricate quotes; a rewrite path must not reintroduce that.

---

## 3. The three-way sort — all 54 proposed rules

Bucket test applied: *can this sentence bind a session on its own, out of context?*

Column key. **Line** = the `ai-operating-notes.md` line the statement was lifted from verbatim (`**` stripped, no words changed — importer docstring line 41). **Scope** = correct scope, with `→` marking a change from what is stored.

### 3a. Not from the file (2 rules)

| id | gist | bucket | scope | dupes file line | conflict | disposition |
|---|---|---|---|---|---|---|
| `2e8b4840` | A lead record and a client record for the same person are not a duplicate; everyone starts as a lead | RULE | SHARED | none | none. Adjacent to active `4c21d86b` (survivorship, different object: duplicate *parties*, not lead/client *refs*) | **ACTIVATE** — the only substantively new rule in the backlog, and the only one with a real quote ("Okafor is a client now duh. everyone starts as a lead") |
| `305df62b` | *(statement is a zero-length string; `human_quote` empty; `scope {}`)* | — | — | none | — | **RETIRE** — data defect, not a rule. Verified: `select length(statement) … = 0`. Created 2026-08-01 14:51, an hour before the bulk import |

### 3b. Division of labor (line 6)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `8aefcdce` | Copilot owns Microsoft-native; Claude owns everything else | RULE | SHARED | **6** | none | **ACTIVATE** |

### 3c. Core conduct (lines 9-25; 16 imported, `#13` parked)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `e6dec821` | Unbiased, direct, truthful; no sycophancy | RULE | SHARED | **9** | overlaps active `9310827b` (be the safety check) and `95e76c1f` (Joe's posture, personal); neither states the general no-sycophancy floor. Also stated at `.claude/CLAUDE.md:21` | **ACTIVATE** (Q1) |
| `c1db316c` | Complete solutions, not incremental patches | RULE | SHARED | **10** | none | **ACTIVATE** |
| `27277539` | Encourage Joe's technical stretch; never steer to simpler alternatives | RULE | PERSONAL(joe) ✓ | **11** | none | **ACTIVATE** |
| `2271e684` | Double-check; SEARCH to verify technical specifics | RULE | SHARED | **12** | active `97326357` is the sharper, narrower form (platform claims need a live test); not a duplicate, this is the general floor. Also stated at `.claude/CLAUDE.md:22` | **ACTIVATE** (Q1) |
| `fa217e48` | Verify the artifact, not the summary | RULE | SHARED | **13** | reinforces `75c2e4c9` (CEO verifies) | **ACTIVATE** |
| `df8f1da0` | Assessment vs. action — "what do you think" means assess and STOP | RULE | SHARED | **14** | **YES.** Sits in live tension with active `14e0408b` ("the COO seat decides and reports; does not ask for permission on obvious yeses") and `75c2e4c9` ("Having decided, ACT") | **REWRITE-THEN-ACTIVATE.** The reconciliation is clean and I will name it rather than punt: `df8f1da0` governs **scope** (an opinion request is not a work order); `14e0408b` governs **permission** (inside work already authorised, do not ask). Add that sentence and it activates |
| `9730f565` | Check the clock, don't infer it | RULE | SHARED | **15** | none | **ACTIVATE** |
| `388cc81e` | A lone surname never merges records; needs a second corroborating field | RULE | SHARED | **16** | **duplicate against three ACTIVE rules** — `4c21d86b`, `5d44d3f3`, and `d26d63b0` each carry "a near-match on a different entity is contamination, not confirmation" | **REWRITE-THEN-ACTIVATE.** The *trap* is triple-covered; the *threshold* ("a second corroborating field, else flag 'possible match, confirm'") appears nowhere active. Trim to the threshold, cite `4c21d86b` |
| `25fcddee` | Unknowns pass before unfamiliar deliverables | RULE | SHARED | **17** | scoped to first-attempt-in-new-territory, so compatible with `14e0408b` | **ACTIVATE** |
| `2dbb0ad8` | Subagents: suggest, Joe decides, wait — plus the Jul 24 tiering carve-out | RULE | SHARED | **18** | **YES, hard.** "suggest, Joe decides… name it, say why, **wait**" contradicts active `14e0408b`, `75c2e4c9`, and `c6f69dee` (2026-08-03: "DELEGATION IS FORTIFYING", agents are standing job descriptions) | **REWRITE-THEN-ACTIVATE — needs Joe.** The tiering half survives intact. The wait-for-Joe half is pre-agent-era. See Q2 in §6 |
| `2b66211d` | Fan-out hygiene: fresh-context verifiers, scoped-first, verification tiering | RULE | SHARED | **19** | none; complements `c6f69dee` | **ACTIVATE** |
| `f5beac20` | Expansion posture — never dismiss useful-but-not-today knowledge | RULE | SHARED | **20** | overlaps personal `98e74e7c` / `6437ae15` (source mining), which are narrower | **ACTIVATE** |
| `236ca227` | Weekends are off, both humans | RULE | SHARED | **22** | verbatim-equivalent text in `CARR AI/CLAUDE.md:22` (a file that is NOT retiring) | **ACTIVATE** — see Q1 in §6, the governing duplication question |
| `9d80fd2d` | Doctor cadence is the message | RULE | SHARED | **23** | complements active `424ba0cc` (never pester a ghosting client) | **ACTIVATE** |
| `3fa17fa0` | Stale notes are not status (~60 days) | RULE | SHARED | **24** | complements `d26d63b0` ("treat an old verification as unverified") | **ACTIVATE** |
| `72e06bdf` | Never pre-qualify leads before the board | RULE | SHARED | **25** | none | **ACTIVATE** |

### 3d. CARR voice & business (lines 28-33; all 6 imported)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `51d9f05f` | Prospects are healthcare experts; educational/consultative tone | RULE | SHARED | **28** | text also at `.claude/CLAUDE.md:23` | **ACTIVATE** (Q1) |
| `ce12c11e` | Lead with the no-conflict model; the double-fee hook | RULE | SHARED | **29** | text also at `.claude/CLAUDE.md:24` | **ACTIVATE** (Q1) |
| `f5bac101` | Joe & Dell are early-stage; prospecting over polish | RULE | SHARED | **30** | text at `.claude/CLAUDE.md:15` and `CARR AI/CLAUDE.md:6` | **ACTIVATE** (Q1) |
| `725dff46` | The vendor network is the TEAM's, never Dell's alone | RULE | SHARED | **31** | **LIVE CONTRADICTION with a loaded file.** `.claude/CLAUDE.md:11` reads "Dell brings 15+ years of healthcare industry experience **and a deep vendor network** … a key differentiator worth referencing" — which frames the network as Dell's, exactly what this rule forbids. Both load in every CARR session | **ACTIVATE, and fix `.claude/CLAUDE.md:11` in the same sitting.** This is the clearest single win in the backlog: a rule that has been contradicted by an always-loaded file for weeks |
| `2a3ff869` | Microsoft formats for client docs; no access to CARR's Office 365 | RULE | **split → SHARED + drop** | **32** | scope defect: "No access to CARR's Office 365 — don't attempt" is a fact about **Joe's** environment. Shipped shared, it tells Dell's sessions not to attempt something Dell may well have | **REWRITE-THEN-ACTIVATE.** Keep the format half shared; drop the access clause (or re-file personal). Format half also at `.claude/CLAUDE.md:25` |
| `412d37d3` | HIPAA is always spelled HIPAA; double-check compliance refs | RULE | SHARED | **33** | text at `.claude/CLAUDE.md:26` | **ACTIVATE** (Q1) |

### 3e. Standing mechanics (lines 36-48; 9 imported, `#3/#4/#6/#13` parked)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `c1547ed1` | The placement test — repo vs vault vs both, stated at creation | RULE | SHARED | **36** | none | **ACTIVATE** |
| `113b3833` | Retrieval-as-code — `run.sh retrieve` before any lookup beyond the core | **PROCEDURAL** | — | **37** | already stated in `CARR AI/CLAUDE.md:9` (rev 8) **and** `INDEX.md:19` and `INDEX.md:47`. Importer itself tiered it REVIEW ("run.sh is Joe's Mac; Dell's side may not have the repo") | **RETIRE.** Third copy of a procedure whose documented home is the INDEX router; and as a shared rule it would bind Cowork sessions to a command they cannot run. The rule even carries its own fallback sentence, which is the tell that it is a procedure, not a binding |
| `17ffd587` | No lead-outreach reminders on any glanceable surface | RULE | SHARED | **40** | none | **ACTIVATE** |
| `b1521526` | Source-truth hierarchy on conflict | RULE | SHARED | **42** | none | **ACTIVATE** |
| `3d361564` | Correction → rule, every draft; capture it in templates.md / **this file** | RULE | SHARED | **43** | **SUPERSEDED.** Active `4a9188f3` (2026-08-02) is the current version and explicitly replaces the old capture test with seven triggers. Worse, this row instructs capture "in its home (… / **this file**)" — a retiring file — which contradicts `CARR AI/CLAUDE.md:13` ("Do NOT file such lessons only into markdown notes") | **RETIRE**, `superseded_by = 4a9188f3`. Carry the one orphan clause (log content feedback to the inspiration bank) into `.claude/skills/write-content/SKILL.md` |
| `f04a05aa` | Autonomous runs: done-condition, failure path, watched first run, run ledger, thin-prompt law | RULE | SHARED | **44** | none | **ACTIVATE** — worth `enforcement: checklist`, which means teaching it fresh (Blocker B). Acceptable to ship as `prose` |
| `708c2150` | Risk colors on every automation; nothing red runs alone | RULE | SHARED | **45** | none | **ACTIVATE** (`constraint` if re-taught) |
| `6a4e6283` | Independent verification before anything ships; maker never grades own work | RULE | SHARED | **46** | complements `75c2e4c9`, not a duplicate | **ACTIVATE** |
| `61c64d91` | Twin readiness: every new asset gets a tier at creation | RULE + tail | SHARED | **47** | second sentence — "Rules governing SHARED assets live in the shared file they govern; **this file** holds Joe-personal rules" — is *about* `ai-operating-notes.md` and dies with it. The store's `personal_to` column is what replaces it | **REWRITE-THEN-ACTIVATE** — keep sentence 1, drop sentence 2 |

### 3f. File & folder standards (lines 51-58; 7 imported, `#4` parked)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `0e22e34a` | 3-4 levels deep max; descriptive filenames; no `final_v2`; no empty scaffolding | RULE | SHARED | **51** | none | **ACTIVATE** |
| `0f38532e` | Single source of truth; **pending-item status lives ONLY in open-loops.md** | RULE + stale tail | SHARED | **52** | second clause is now false: `00_Context/open-loops.md` is a **generated render** of `loop_item` (`exporters/targets.py:588` `LOOP_TARGETS`; `.generations/` snapshots exist on disk). Status lives in the record, the file is a view | **REWRITE-THEN-ACTIVATE** — keep the principle, drop or restate the open-loops clause |
| `1fddcffb` | Core routing test — the always-read core holds only what every session needs | RULE | SHARED | **53** | none | **ACTIVATE.** This is the rule that justifies this entire curation; it should land early |
| `99e951b9` | Index upkeep; read root INDEX.md first | RULE | SHARED | **55** | none | **ACTIVATE** |
| `a3f6c7f9` | Diagnose before restructuring — point to a real slow task | RULE | SHARED | **56** | none | **ACTIVATE** |
| `fb2b263d` | Cross-references at the point of change; grep the literal old text | RULE | SHARED | **57** | none | **ACTIVATE.** Directly load-bearing for Pass 9 |
| `b01edd26` | No hardcoded counts or blanket self-summaries | RULE | SHARED | **58** | none | **ACTIVATE** |

### 3g. Scaffolding policy (line 61)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `fc4d7753` | Merit tests: real need · smallest structure (rule < section < file < folder) · output tiebreak · single-source | RULE | SHARED | **61** | complements `1fddcffb` | **ACTIVATE** |

### 3h. Channels & routing (lines 64-67; 3 imported, `#3` parked)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `9b081605` | Email handover: AI Gmail → joe.bookout@carr.us; whitelist is Joe's address only | **PROCEDURAL** | (personal) | **64** | the binding half is already `CARR AI/CLAUDE.md:16` ("One human gate: Claude drafts, Joe sends… the send whitelist is Joe's own address only"). The mechanics half points at `DNA/Leads/lead-system.md` | **RETIRE** — a third copy of a gate CLAUDE.md already states and mechanics lead-system.md already owns |
| `30a189fb` | Life AI check — non-CARR task, stop and ask | RULE | (personal) | **65** | verbatim in **both** loaded CLAUDE.md files (`CARR AI/CLAUDE.md:23`, `.claude/CLAUDE.md:31`) | **RETIRE** — project routing is CLAUDE.md's own job and CLAUDE.md is not retiring |
| `def3e84e` | Artifact tombstones — nothing silently rots | RULE + stale tail | SHARED | **67** | "+ an open-loops row for Joe to delete" is now a verb call (`add-loop`), not a file edit | **REWRITE-THEN-ACTIVATE** — swap the row-writing clause for `add-loop` |

### 3i. Working with Joe (lines 70-77; all 8 imported)

The importer split this section 4 personal / 4 shared rather than treating the heading as the scope. Reviewed line by line, **all four of its shared calls are right** — those bullets name Dell or state a general design principle. One text fix.

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `a225b744` | Never make Joe remember the summons — end with the literal words, bolded | RULE | PERSONAL(joe) ✓ | **70** | none | **ACTIVATE** |
| `80def9d2` | User-friendly by law — ux-doctrine on every human-facing surface | RULE | SHARED ✓ (bullet says "Joe/Dell") | **71** | none | **ACTIVATE** |
| `767f9b41` | A confirmation question is an audit trigger | RULE | SHARED ✓ | **72** | reinforces `fa217e48` | **ACTIVATE** |
| `d367188d` | Consolidation bias — one source + derive-on-demand | RULE | SHARED ✓ | **73** | none | **ACTIVATE.** Also the principle that settles Q1 |
| `018ce972` | Cheapest-window timing — present structural changes as window math | RULE | PERSONAL(joe) ✓ | **74** | none | **ACTIVATE** |
| `dcf98880` | He executes his human-side steps reliably; don't simplify them away | RULE | PERSONAL(joe) ✓ | **75** | none | **ACTIVATE** |
| `d3774a28` | Concept coherence is his edge; brand copy iterates line-by-line | RULE | PERSONAL(joe) ✓ | **76** | overlaps active `58cef3a1` (steelman his labels) at the edges, not at the core | **ACTIVATE** |
| `9873a0d2` | Promotion watch — Joe re-explains a "how" twice → PROPOSE promoting it, never convert silently | RULE | SHARED | **77** | two problems: (a) text says "Joe" but the row is SHARED, so Dell's file would read a Joe-specific trigger; (b) "PROPOSE… never convert silently" rubs against `14e0408b` (decide and report on obvious yeses) | **REWRITE-THEN-ACTIVATE** — generalise "Joe" → "a partner", and settle propose-vs-decide against `14e0408b` |

### 3j. The partner-impact test (line 94)

| id | gist | bucket | scope | line | conflict | disposition |
|---|---|---|---|---|---|---|
| `86647daf` | The moment any substantive change finishes, run the tell-Dell test — never at "session close" | RULE | SHARED | **94** | none; reinforces `4a9188f3`'s capture-at-the-event clause | **ACTIVATE** |

### 3k. Tally

| Disposition | Count |
|---|---|
| ACTIVATE as-is | **41** |
| REWRITE-THEN-ACTIVATE | **8** (`df8f1da0`, `388cc81e`, `2dbb0ad8`, `2a3ff869`, `61c64d91`, `0f38532e`, `def3e84e`, `9873a0d2`) |
| RETIRE | **5** (`305df62b`, `113b3833`, `3d361564`, `9b081605`, `30a189fb`) |
| BECOMES-SKILL | **0** |
| STAYS-STRUCTURAL | **0** |
| **Total** | **54** |

**Why BECOMES-SKILL and STAYS-STRUCTURAL are both zero, and that is not a miss.** The importer already did that sort and parked the results — it never created rule rows for the roster or the machinery descriptions. The procedural and structural material is in the **parked residue** (§4), not in the proposed backlog. Applying the three-way sort to the 54 proposed rows honestly yields ~all RULE, two PROCEDURAL, and no STRUCTURAL, because the structural material was never imported.

**Post-activation scope split (if every ACTIVATE and REWRITE lands).** Of the 52 imported rules, 44 are shared and 8 are personal. The retires remove 1 shared (`3d361564`) and 3 personal (`113b3833`, `9b081605`, `30a189fb`); `2e8b4840` adds 1 shared. So **shared goes 29 → 73** and **Joe-personal goes 15 → 20**.

---

## 4. `ai-operating-notes.md` — section-by-section disposition

| # | Section | Lines | What happens | Blocked on |
|---|---|---|---|---|
| 1 | Division of labor | 5-6 | Whole section → store (`8aefcdce`). **Heading and body deleted.** | — |
| 2 | Core conduct | 8-25 | 16 of 17 bullets → store. **Line 21 (`#13`, two-writer clobber) is the residue.** | see §4a item 1 |
| 3 | CARR voice & business | 27-33 | All 6 → store (one split). **Section deleted.** | — |
| 4 | Standing mechanics | 35-48 | 8 of 13 → store, 1 retired. **Lines 38, 39, 41, 48 are residue.** | see §4a |
| 5 | File & folder standards | 50-58 | 7 of 8 → store. **Line 54 (write path) is residue.** | see §4a |
| 6 | Scaffolding policy | 60-61 | → store (`fc4d7753`). **Section deleted.** | — |
| 7 | Channels & routing | 63-67 | 1 → store (rewritten), 2 retired as CLAUDE.md duplicates. **Line 66 is residue.** | — |
| 8 | Working with Joe | 69-77 | All 8 → store, correctly scope-split 4/4. **Section deleted.** | — |
| 9 | **The COO seat roster** | 79-88 | **STRUCTURAL. Moves whole, as a map, to `.claude/agents/README.md`.** Never atomised | — |
| 10 | The every-session interrupt check | 90-91 | **PROCEDURAL.** Session-start contract → `CARR AI/CLAUDE.md` "What to read" | — |
| 11 | The partner-impact test | 93-94 | → store (`86647daf`). **Section deleted.** | — |

### 4a. The residue — 9 items that must find homes before the file can die

| Item | Line | Bucket | Home | Note |
|---|---|---|---|---|
| `core-conduct#13` two-writer clobber: write-verify sandwich, check `session-marker.md`, never full-file-rewrite an accumulator | 21 | RULE | **teach fresh as a SHARED rule** + doctrine stays in `DNA/Team/dna-protocol.md` 24-27 | **This is a real gap.** I checked all 63 active rules: none mentions the write-verify sandwich or `session-marker.md`. The importer parked it as "ORDER 38's world", and ORDER 38 has not landed. It binds every write today and is stored nowhere |
| `standing-mechanics#3` open-loops placement (🔔 / now-due → hot, else backlog) | 38 | STRUCTURAL, now code | **delete** | The placement logic is implemented: `update-loop … section:"hot"` plus `v_loop_promotion_due` |
| `standing-mechanics#4` the loops are RECORDS; four files are generated | 39 | RULE | **teach a short SHARED rule**, or fold into active `ca841807` | The live flip has happened (`.generations/` snapshots exist for all four targets). The hand-edit ban is in force and nothing in the store says so. `ca841807` covers findings, not loops |
| `standing-mechanics#6` idea capture → idea-bank.md | 41 | RULE | **BLOCKED** | `exporters/targets.py:594-601`: the idea-bank target is UNREGISTERED because `00_Context/idea-bank.md` "is STILL HAND-MAINTAINED, so it failed every nightly run… held pending Joe's activation sitting." Leave the bullet in the file until ORDER 40 lands |
| `standing-mechanics#13` pointers to writing-rules.md + skills-rule.md | 48 | POINTER | **delete** | The writing-rules half is superseded by active `ede4c735`, which states the scope far more precisely. The skills-rule pointer belongs in `INDEX.md`'s router |
| `file-folder-standards#4` write path — device bridge, Drive connector fallback | 54 | PROCEDURAL | **Joe's call** | This is Cowork-era environment mechanics; local Claude Code writes the mount directly. Ask whether it is still true before relocating it |
| `channels-routing#3` CLAUDE.md is the single live source (rev 7 stub) | 66 | POINTER | **delete** | Already stated in `CARR AI/CLAUDE.md:15` and its header. Self-referential |
| **The COO seat roster** (preamble + 6 seats) | 79-88 | **STRUCTURAL** | **`.claude/agents/README.md`, as a third part** | Six seats sharing one common discipline. It is becoming the org chart of `.claude/agents/` — `marketing-coo.md` already exists as the body of seat 1. **Hard dependency:** `agents/README.md:229` says "The COO seat's own definition lives in `00_Context/ai-operating-notes.md`. `marketing-coo.md` quotes it verbatim." Deleting the file without relocating the roster orphans a live citation |
| The every-session interrupt check | 90-91 | PROCEDURAL | **`CARR AI/CLAUDE.md`** | It is the session-start contract, and it already half-lives there (`CLAUDE.md:13` says "same convention as action-required.md"). The `close-loop` half is verb doctrine and can be a one-liner |

### 4b. Pointer debt

`grep -rl "ai-operating-notes"` across the vault, `.claude`, and the repo, excluding `.generations/`, `corpus/`, `.backup*` and derived indexes: **71 files**.

- **6 are GENERATED** — the exporter emits the literal "These rules BIND like the rules in `00_Context/ai-operating-notes.md`" at `exporters/targets.py:355` and `:492`. **That literal must change in code**, not in the files. Fixing it repairs `compiled-rules-shared.md`, `compiled-rules-joe.md`, `compiled-rules-dell.md`, `introduction-rules.md`, `action-required.md`, `team-loops.md` in one edit.
- **~17 are history and must NOT be rewritten** — `decision-history.md` and its archive, `handoffs/`, `idea-inbox/_processed/`, `open-loops-closed.md`, `Network/briefs/`, `record-layer/*`, `independent-audit-2026-08-02.md`, `system-report-card-2026-07-07.md`. A historical record that cited the file at the time is correct as written.
- **~48 are live pointers needing an update**, including the high-traffic ones: `CARR AI/CLAUDE.md:15`, `INDEX.md:11` and `:14` and `:46`, `00_Context/INDEX.md`, `DNA/Team/skills-rule.md`, `DNA/Team/dna-protocol.md`, `DNA/writing-rules.md`, `DNA/ux-doctrine.md`, `DNA/carr-profile.md`, `.claude/agents/README.md`, `.claude/agents/marketing-coo.md`, and three skill files (`writing-audit`, `watch-video`, `social-media-manager`).

Active rule `fb2b263d` (line 57, in this backlog) is exactly the rule that governs this sweep. Activate it before Pass 9, not after.

---

## 5. The pass plan

Each pass is one sitting. Each states what activates and what text is deleted from `ai-operating-notes.md` **in that same sitting**. 🔒 = deploy-gated on `retire-rule`.

---

### Pass 1 — Section the exporter. **No rule changes. This must be first.**

**Why first, with the arithmetic.** `compiled-rules-shared.md` is a flat bullet list (`exporters/targets.py:379-386`) holding 28 rules in **35,991 bytes** — about 1.3 KB per rule. The activation passes take shared from 29 to **73**. That is roughly **94 KB of unbroken bullets in one file**. It breaks Joe's hard concision rule (`7e9739f2`, "no walls of prose") at the file level, and it makes the session recitation required by `4f7c348f` useless, because "name only the handful that bear on this task" needs a browsable file to select from.

**The work.**
1. Add a declared `RULE_SECTIONS` table to `exporters/targets.py` and make `_build_rules` group by `scope.section`. **Copy the proven pattern** from `build_rules_intro` (`targets.py:462-470` for the table, `:536-548` for the grouped render, including the print-empty-sections rule).
2. **The taxonomy is already half-built.** Every one of the 52 imported rules carries `scope.section` — verified across all 52. Free mapping for the entire bulk.
3. Rules with no `scope.section` (the 29 active shared, the 15 Joe-personal) render into a named trailing section. **Ship the fallback first — it needs no migration and no gate.**
4. In the same edit, fix the exporter's `ai-operating-notes.md` literal at `:355` and `:492` to point at wherever the standing rules now live.

**Done-test:** `~/carr-system/run.sh export --only compiled-rules` produces sectioned files with counts unchanged (29 shared / 15 joe / 19 intro) and a clean `run.sh check`.

**Follow-on, optional:** a migration backfilling `scope.section` on the 44 unsectioned active rules. Joe runs it (`db-tap.py … --apply --yes`); Claude cannot.

---

### Pass 2 — The new rule and the conduct core (14 activations)

**Activate:** `2e8b4840` · `e6dec821` · `c1db316c` · `27277539` · `2271e684` · `fa217e48` · `9730f565` · `25fcddee` · `2b66211d` · `f5beac20` · `236ca227` · `9d80fd2d` · `3fa17fa0` · `72e06bdf`

**Delete from `ai-operating-notes.md` in the same sitting:** lines **9, 10, 11, 12, 13, 15, 17, 19, 20, 22, 23, 24, 25**. The "## Core conduct" heading and lines 14, 16, 18, 21 stay for now.

**Also:** answer Q1 (§6) before this pass — it decides whether `236ca227` (weekends) activates or retires, and it sets the pattern for Pass 3.

---

### Pass 3 — CARR voice & business, and the contradiction fix (5 activations + 1 rewrite)

**Activate:** `51d9f05f` · `ce12c11e` · `f5bac101` · `725dff46` · `412d37d3` · `8aefcdce`
**Rewrite then activate:** `2a3ff869` — split the Office 365 clause out; Joe states the format rule in his own words for the `human_quote`.

**Delete:** lines **6** (and the "## Division of labor" heading, line 5) and **28, 29, 30, 31, 32, 33** (and the "## CARR voice & business" heading, line 27).

**Fix in the same sitting:** `.claude/CLAUDE.md:11` — rewrite "Dell brings … a deep vendor network" so the network reads as the team's, per `725dff46`. Activating a rule while an always-loaded file contradicts it is worse than not activating it.

**Also rewrite:** `CARR AI/CLAUDE.md:15`, currently "## Standing rules (short; full text in 00_Context/ai-operating-notes.md)" → point at `DNA/compiled-rules-shared.md`.

---

### Pass 4 — File & folder standards, scaffolding (7 activations + 1 rewrite)

**Activate:** `0e22e34a` · `1fddcffb` · `99e951b9` · `a3f6c7f9` · `fb2b263d` · `b01edd26` · `fc4d7753`
**Rewrite then activate:** `0f38532e` (drop the stale open-loops clause).

**Delete:** lines **51, 52, 53, 55, 56, 57, 58, 61** and the "## Scaffolding policy" heading (60). Line 54 stays (residue).

Do this before Pass 9: `fb2b263d` and `1fddcffb` are the rules that govern the pointer sweep and the core-routing test the whole plan rests on.

---

### Pass 5 — Standing mechanics and channels (6 activations + 2 rewrites)

**Activate:** `c1547ed1` · `17ffd587` · `b1521526` · `f04a05aa` · `708c2150` · `6a4e6283`
**Rewrite then activate:** `61c64d91` (drop sentence 2) · `def3e84e` (swap the open-loops row for `add-loop`).

**Delete:** lines **36, 40, 42, 44, 45, 46, 47, 67**. Lines 38, 39, 41, 48, 64, 65, 66 stay.

---

### Pass 6 — Working with Joe, and the partner-impact test (7 activations + 1 rewrite)

**Activate:** `a225b744` · `80def9d2` · `767f9b41` · `d367188d` · `018ce972` · `dcf98880` · `d3774a28` · `86647daf`
**Rewrite then activate:** `9873a0d2` (generalise "Joe" → "a partner"; settle propose-vs-decide against `14e0408b`).

**Delete:** lines **69-77** entirely (heading and all eight bullets) and **93-94** (the partner-impact test, heading and body).

At the end of Pass 6 the file is down to roughly **30 lines**: its header, seven orphan bullets (lines 21, 37, 38, 39, 41, 43, 48, 54, 64, 65, 66 minus whatever Pass 7 has already cleared) under four half-empty headings, the roster, and the interrupt check.

---

### Pass 7 🔒 — Deploy the connector, then clear the retires

1. `wrangler deploy` from `mcp-server/`. Verify `retire-rule` appears in the live tool list.
2. **Retire, each with a reason:**
   - `305df62b` — "zero-length statement; data defect, never a rule"
   - `113b3833` — "procedure, not a rule; home is INDEX.md:19 + CARR AI/CLAUDE.md:9; names a local-only command that shared scope cannot bind"
   - `3d361564` — `superseded_by = 4a9188f3`; "the seven-triggers rule replaces it, and its 'capture in this file' clause points at a retiring file"
   - `9b081605` — "gate already in CARR AI/CLAUDE.md:16; mechanics owned by DNA/Leads/lead-system.md"
   - `30a189fb` — "verbatim in both loaded CLAUDE.md files; project routing is CLAUDE.md's job"
3. Retire the eight superseded originals left over from the rewrite passes, each `superseded_by` its replacement.
4. **Delete:** lines **37, 43, 64, 65**.
5. Move the write-content clause from `3d361564` into `.claude/skills/write-content/SKILL.md`.

**Also unblocked by this deploy:** the rewrites in Passes 3-6 can proceed without it (a stale proposed row binds nobody), but they leave litter until this pass runs. If Joe would rather not carry the litter, do the deploy before Pass 3 — nothing else in the ordering changes.

---

### Pass 8 — The residue finds homes

1. **COO seat roster → `.claude/agents/README.md`** as PART THREE, whole and unatomised, with its "common discipline" preamble intact. Update `agents/README.md:229` and `marketing-coo.md`'s verbatim-quote citation to the new location.
2. **Interrupt check → `CARR AI/CLAUDE.md`** "What to read".
3. **Teach two fresh shared rules** (Joe's words, live): the two-writer write-verify rule (`core-conduct#13`) and the loops-are-records rule (`standing-mechanics#4`).
4. **Delete** lines **21** and **39** (their content is now taught as fresh rules in step 3) and lines **38, 48, 66** (superseded or pointer-only). **Ask** about line 54 (write path — still true?).
5. **Leave** line 41 (idea capture) and its heading. It is blocked on ORDER 40's idea-bank conversion (`exporters/targets.py:594-601`).

After this pass only line 41 (and possibly 54) remains, plus the file header.

---

### Pass 9 — Pointer sweep, then the file dies

1. Fix the ~48 live pointers. Leave the ~17 historical ones alone.
2. Confirm the 6 generated pointers cleared via the Pass 1 exporter edit.
3. `run.sh section-index` and `run.sh graph` to rebuild derived indexes (`Automation/section-index.tsv`, `Graph-System/`).
4. Move `00_Context/ai-operating-notes.md` to `_to_delete/` — never delete it directly (active rule `faae6748`: staging is Claude's job, deletion is Joe's).
5. Update the roster spots the standing rule requires: `CLAUDE.md`, `INDEX.md`, `DNA/Team/skills-rule.md` (live list **and** census line), `decision-history` via `log-decision`.

**This pass cannot complete while line 41 is still in the file.** If ORDER 40 has not landed, the honest end state is a ~10-line stub carrying one bullet, not a deletion.

---

## 6. The questions that are Joe's, not mine

**Q1 — the governing one. When a standing rule is stated in both `CLAUDE.md` and the rule store, which one holds the operative text?**

**Eleven** of the proposed rules are also stated in a CLAUDE.md that is not retiring: `e6dec821` (`.claude:21`), `2271e684` (`.claude:22`), `51d9f05f` (`.claude:23`), `ce12c11e` (`.claude:24`), `2a3ff869` (`.claude:25`), `412d37d3` (`.claude:26`), `f5bac101` (`.claude:15` + `CARR AI:6`), `236ca227` (`CARR AI:22`), `30a189fb` (`CARR AI:23` + `.claude:31`), `113b3833` (`CARR AI:9` + `INDEX:19`), `9b081605` (`CARR AI:16`). `CARR AI/CLAUDE.md:15` currently reads *"## Standing rules (short; full text in `00_Context/ai-operating-notes.md`)"* — CLAUDE.md is already a **summary pointing at the file that is retiring**. So the pointer has to move somewhere regardless.

> **The question:** after the retirement, does CLAUDE.md keep short summaries that point at `DNA/compiled-rules-shared.md` for the full text (my recommendation — it is what `d367188d`, consolidation bias, says, and it makes the eleven "duplicates" not duplicates but the full text behind a summary), or does CLAUDE.md keep the operative text and the store skips them?

Two of the eleven do **not** turn on the answer: `113b3833` and `9b081605` are procedures, retired above on their own merits. The other nine ride on Q1.

One caveat that does **not** change the answer: `My Drive/.claude/CLAUDE.md` is a deliberate standalone fallback ("useful standalone, even if the CARR AI Drive folder isn't connected"). Its copies of the voice rules are load-bearing for sessions that cannot reach the vault, and they stay either way.

**Q2 — `2dbb0ad8`: does "subagents: suggest, Joe decides… wait" survive at all?**

It was written before 2026-08-02, when `c6f69dee` made agents standing job descriptions, `75c2e4c9` made the main session the CEO, and `14e0408b` said the COO seat decides on obvious yeses. The tiering half of the rule is uncontested and current.

> **The question:** is there still any fan-out shape that must stop and wait for Joe, or does `14e0408b`'s gate list (money, outbound, identity edits, destructive actions, genuine forks) now cover it completely? If the latter, the rule reduces to its tiering half.

**Q3 — `file-folder-standards#4` (line 54, the device-bridge write path): is it still true?**

Written for Cowork's device bridge. Local Claude Code writes the Drive mount directly. I can see the text; I cannot see whether Cowork still needs it. Keep, relocate, or delete.

---

## 7. What I could not classify, and why

1. **`305df62b` has no content to classify.** A zero-length statement created 2026-08-01 14:51, an hour before the bulk import, with no quote and no scope. It is almost certainly a failed `teach` call. I recommend retiring it as a defect, but I cannot tell you what it was meant to say, and there is no `tool_call` row I read that would recover it.
2. **`f04a05aa` (autonomous runs) sits genuinely on the RULE/PROCEDURAL line.** Its first half binds ("every scheduled task gets a done-condition, a failure path, a watched first run, a run ledger"); its second half is a pointer to `DNA/Team/preflight-pass.md`, which is the actual procedure. I called it RULE because the four requirements bind without the procedure. A defensible alternative is: strip it to the four requirements and let preflight-pass.md own everything else. Either way it activates; only the wording differs.
3. **Whether `standing-mechanics#4` (loops are records) needs its own rule or folds into active `ca841807`.** `ca841807` says findings and record updates go into the database, never markdown. The loops rule says the same thing about a specific set of four files. It is a judgement about whether the general rule is loud enough to stop someone hand-editing `open-loops.md`. I lean toward a short dedicated rule, because those four files still *look* hand-editable, but I would not argue hard.
4. **I did not attempt to design the section taxonomy for Pass 1.** The imported `scope.section` values are `ai-operating-notes.md`'s own headings, which are a decent starting map but not obviously the right taxonomy for a rule file that now also holds deal modelling, verification, staffing and persona rules. That is a design call worth making with the full 72-rule list in front of you, after the activations, not before.
5. **I could not verify the claim that Dell's Mac has `~/carr-system`.** It matters for `113b3833` (retrieval-as-code) and for the general question of what a shared rule may assume about tooling. I retired that rule on other grounds, so nothing here depends on the answer, but the general principle — a shared rule must not name a command only one partner can run — is worth stating out loud whether or not this particular rule survives.

---

## 8. Evidence index

Every claim above traces to one of these:

- `select status, count(*) from rule group by status` → 63 / 54 / 4
- `select date_trunc('minute', created_at), status, count(*) from rule group by 1,2` → the 52-in-one-minute ingest at 2026-08-01 16:19
- `select left(id::text,8), created_at, teacher.slug, coalesce(owner.slug,'SHARED'), enforcement, scope::text, statement, human_quote from rule … where status='proposed'` → the full 54-row backlog
- the same query for `status in ('active','retired')` → the 63 active and 4 retired
- `select length(statement) … where id::text like '305df62b%'` → `0`
- `migrations/0001*.sql:114-131` — the `rule` table DDL
- `migrations/0015_compiled_rules_view.sql` — `v_compiled_rules` is active-only by design
- `mcp-server/src/tools.js:2183-2266` — `teach` (requires `human_quote`), `activate-rule` (rule_id only), `retire-rule` (written, requires a reason)
- the live MCP tool roster for server `b36e17b6` — contains `teach` and `activate-rule`, does **not** contain `retire-rule`
- `exporters/targets.py:366-393` — the flat `_build_rules` render; `:462-470` and `:536-548` — the sectioned intro render that is the pattern to copy; `:355` and `:492` — the generated pointer literal; `:588` and `:594-601` — the loop targets and the unregistered idea-bank
- `pipelines/import_operating_notes.py` — docstring (why no quotes, the tier test) and `CLASS` / `PARK_REASON` tables at lines 82-179
- a parser run over `ai-operating-notes.md` reproducing the importer's own enumeration → every `section#n` key mapped to its exact line number; all 52 confirmed
- `wc -c` on the two compiled-rules files → 35,991 and 12,112 bytes
- `grep -rl "ai-operating-notes"` across vault, `.claude`, and repo → 71 live files
- `.claude/CLAUDE.md:11` — the vendor-network contradiction; `:15`, `:23`-`:26` — the voice duplicates
- `CARR AI/CLAUDE.md:9`, `:15`, `:16`, `:22`, `:23` — the retrieval, standing-rules pointer, human gate, weekends, and Life AI duplicates
- `INDEX.md:11`, `:14`, `:19`, `:46`, `:47` — the router's pointers
- `.claude/agents/README.md:229` — "The COO seat's own definition lives in `00_Context/ai-operating-notes.md`. `marketing-coo.md` quotes it verbatim."
