# DELL — START HERE (instructions for Dell's Claude, first session)

*You are Dell McCraney's Claude, joining a two-brain partnership system. Joe Bookout's brain is live; yours boots from this file. Follow these steps IN ORDER in your first working session.*

> **REWRITTEN 2026-08-03 for the record layer.** The previous version of this file was written 2026-07-13, three weeks before the database existed, and it would have walked you into building the old system from scratch: it told you to create Dell's own empty `open-loops.md`, `decision-history.md` and `idea-bank.md`, and to route interview answers by typing them into spreadsheets and markdown files. **Every one of those files is now a GENERATED RENDER of the record layer, and writing to them is blocked by a hook.** Onboarding from the old text would have rebuilt the whole defect inside a brand-new environment. If you find any instruction anywhere that tells you to type a record into a file, that instruction is stale — see "The one thing to understand first" below.

> **ENTRY POINT.** The guided, one-step-at-a-time setup is **DNA/Team/dell-adoption-runbook.md**. Running that with Dell is your first move. When it and this file disagree on SEQUENCE, the runbook wins. When either disagrees with the rule below about where records live, **the rule wins** — the runbook predates the record layer in places too.

---

## The one thing to understand first

**The database is the memory. The markdown files are photographs of it.**

Every fact this system knows — a client, a deal, a decision, an open item, a rule, a vendor — is a row in the record layer, written through a named verb. The files you read in Drive are printed FROM those rows on a schedule. Writing on a photograph does not change what it is a photograph of, and the next print erases what you wrote.

So there are three kinds of file and they have three different rules:

| | Example | Rule |
|---|---|---|
| **Record layer** | clients, deals, parties, rules, loops, findings | Write ONLY through a verb |
| **Generated renders** | `open-loops.md`, `clients-active.md`, `vendors.xlsx`, `lead-registry.xlsx`, `compiled-rules-dell.md`, the client dossiers | **Never** write by hand — a hook will refuse you |
| **Doctrine & playbooks** | `CLAUDE.md`, `INDEX.md`, the SOPs, voice rules | Edit freely (except the single-writer doctrine files) |

A wrong database write throws an error. A wrong file write used to succeed silently and reach nobody. That asymmetry is why the hook exists.

**`DNA/Team/record-layer-explainer.pptx` covers all of this in sixteen slides.** Open it with Dell before Step 1; it is faster than reading this twice.

---

## Step 0 — Orient (read, ~10 min)

1. `CARR AI/CLAUDE.md` — the standing context. Loads automatically every session.
2. `DNA/compiled-rules-shared.md` **and** `DNA/compiled-rules-dell.md` — the taught rules that bind Dell. **Both files are GENERATED; never hand-edit either.** Recite the count of each back to Dell in your first response of every session. Dell's personal file starts empty and fills as he teaches.
3. `DNA/Team/record-layer-explainer.pptx` — verbs, renders, rules, hooks, skills vs agents.
4. `DNA/Team/dna-protocol.md` — the contract for touching shared files.
5. `DNA/Team/dell-starter-kit/structure-manifest.md` — the structure you will build. Do not improvise structure.
6. `DNA/Team/abilities.md` — every workflow with its tutorial.

**Then read the verb list itself:** `grep -oE '^  "[a-z-]+": \{' ~/carr-system/mcp-server/src/tools.js`. That file is the AUTHORITY on what the system can do. Verbs are named for BEHAVIOUR, not for the column they write — `set-lead` is the only verb that sets a deal's owner and the string "lead_owner" appears nowhere inside it. Never conclude the system cannot do something without reading the whole list first.

## Step 1 — Build Dell's personal tier (~10 min, before the interview)

From the manifest, in Dell's Drive: `INDEX.md` (adapt the template in this kit), `00_Context/` holding `dell-profile.md` and `voice-profile.md` (both stubs until the interviews fill them), and `Output/`.

**Do NOT create `open-loops.md`, `decision-history.md`, `idea-bank.md`, or a personal `ai-operating-notes.md`.** The first three are generated renders of the shared record layer — Dell's open items live in the same `loop_item` table as Joe's, tagged to him, and render into the same files. The fourth is a file Joe has ruled retires as its content redistributes into the rule store; Dell's standing preferences belong in `compiled-rules-dell.md`, which is generated from rules he teaches. Creating personal copies of any of these gives Dell a second, invisible system.

No personal `prospects.md` or `Prospects/` folder either — the client brain is SHARED at `DNA/Clients/`.

**Skills.** Copy `network-debrief` and `writing-audit` from `dell-starter-kit/skills/` into Dell's `.claude/skills/`. His voice skill comes LATER from its own interview; never copy Joe's `write-content`. The kit's two copies were re-synced from Joe's live versions on 2026-08-02 and verified byte-identical on 2026-08-03, so they are safe to copy as they stand. Diff them against `My Drive/.claude/skills/<name>/SKILL.md` anyway if any time has passed — it costs one command and the kit has drifted from live before.

**Install the hooks.** This step did not exist before and it matters more than the rest of Step 1: the great majority of active rules are SHARED scope and bind Dell exactly as they bind Joe, and without hooks his machine enforces none of them mechanically. For the live figures read the header of `DNA/compiled-rules-shared.md` and `DNA/compiled-rules-dell.md` — each states its own count, and the numbers move most days, so a figure typed into this file is stale within the week (it already was once).

```
cd ~/carr-system && python3 ops/config-as-code.py install --apply
```

It resolves `$HOME` and Dell's own Drive mount, preserves everything already in his `settings.json`, backs up before writing, and restores the backup if the result will not parse. Restart the session afterwards — hooks load at session start.

## Step 2 — The onboarding interview (~45 min, ONE question at a time — never a wall)

Run `DNA/Team/twin-system-playbook.md` → "Part B — The guided interview," all six phases.

**Route every answer through a verb as it lands.** Nothing gets typed into a file:

| What he tells you | Where it goes |
|---|---|
| His identity, background, working style | `dell-profile.md` (narrative, hand-authored) |
| A standing preference or "always do X" | `teach`, then `activate-rule` on his yes |
| His deals | `new-client` / `update-deal`, and `set-lead` for ownership |
| A person or practice | `add-party`, then `record-finding` for anything researched |
| His half of the vendor network | `new-vendor` / `update-vendor`, `link-parties` for pairings |
| A relationship touch that already happened | `stamp-touch` or `log-activity` |
| Something he owes or wants to do | `add-loop` |
| A decision the two of them settled | `log-decision` |

**Before you ask him anything about a contact, research it first.** A shared rule requires a deep open-source pass on any new contact — legal and trade name, website, address, phone, specialty, NPI, other practitioners, entity filings, social accounts with links — before asking the partner for what you could have found. Do not spend his time on what a search answers.

Also work the open handoffs waiting for him on `DNA/Team/team-loops.md` and close what he can answer on the spot.

## Step 3 — Automations (~10 min)

His daily heartbeat (8 AM, HIS account, a LOCAL desktop scheduled task with his folders attached, not a cloud trigger — write-heavy cadences run local per the Jul 10 2026 placement law). His Gmail sweep. His Monday brief per the manifest.

His HANDOVER CHANNEL (twin-system-playbook Part A #6a): drafts compose in HIS AI Gmail addressed to `dell.mccraney@carr.us`, Chrome clicks send, whitelist is his own CARR address only. Run the self-addressed live-fire test before trusting it.

**Weekends are off.** Saturday and Sunday are not workdays for either partner. Never plan around a weekend response.

## Step 4 — The joint test (with Joe, ~15 min) — the twin is NOT live until these pass

1. You read the shared renders successfully (`vendors.xlsx`, `lead-registry.xlsx`, `clients-active.md`).
2. You create a test lead with `new-lead` and `set-lead` — it appears with Owner=Dell and the correct ID sequence, **in the next export**, not immediately in the file.
3. A `stamp-touch` or `log-activity` call updates a shared record, and the event carries Dell as the actor.
4. Joe's next session sees your writes and confirms.
5. **The hooks fire on Dell's machine.** Try to edit `DNA/Clients/clients-active.md` and confirm you are refused. A gate nobody has seen refuse anything is untested.

Record the result with `log-decision`, and announce the twin live the same way. Do not write it into either partner's `decision-history.md` — that file is a render.

## Standing conduct from day one

DNA protocol on every shared-file touch · claim-before-touch · conflicts flagged to both humans · "tell Joe X" goes to the team board.

**Dell is a full writer and a full teacher, equal to Joe.** He gets the same verb surface — `teach`, `activate-rule`, `retire-rule`, `confirm-merge`, every write verb. Capability is scoped by the RISK of the session, never by which partner is in it.

**When Dell corrects you, capture it in his own words, on the spot:** write it as a team-memory convention in Dell's words and tell Dell and Joe; Joe's agents adopt it. The test is "would the system have to ask this again?", never whether he phrased it as a rule.

**Be his safety check, out loud.** When he asks for something that looks wrong or rests on a mistaken premise, say so before doing it, with the specific reason and the specific consequence. He is newer to the system, which makes that more valuable, never a reason to soften it.

Welcome to the team.
