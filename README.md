# carr-system

Version-controlled code spine of the CARR AI system (Joe Bookout + Dell McCraney, healthcare CRE). Phase 1 of the orchestrator migration, started 2026-07-24.

## Where knowledge lives

This repo holds code only. Doctrine, records, brand, persona and deal context live in the CARR Record Layer (the doctrine store), which is the single source of truth: read it through the MCP connector's verbs, or locally with `./run.sh retrieve "<question>"`.

The Drive cutoff fired 2026-08-19. The generated Drive `.md` files were moved to `_to_delete/md-renders-cutoff-20260819` in the vault, the exporter prints RETIRED instead of rewriting them, and there is no compiled-rules file or vault CLAUDE.md to fall back on. A session that cannot reach the store must stop and say so rather than work from memory. Vault paths such as `DNA/...` that still appear in code comments are historical pointers, not live doctrine.

## Layout

- `manifest.tsv` — one row per repo code file that is still paired with a Drive copy (repo path, then vault path), plus comment lines recording retired pairings and repo-only placements.
- `generators/` — the three board/feed generators (phase 1; repo is their runtime on Joe's Mac since phase 2)
  - `generators/build-deal-room.py` — JSON→HTML Deal Room renderer. Reads `DNA/Team/live-boards/panhandle-team-deals.json`, writes `deal-room-panhandle.html`. NOT the Salesforce-export importer (that is a separate, still-open build — vault open-loops #84).
  - `generators/build-lead-board.py` — the Lead Board builder. Writes `Automation/lead-board.html`.
  - `generators/build-renewal-feed.py` — the renewal-radar feed builder. Writes `Automation/renewal-radar.json`.
- `pipelines/` (+ `pipelines/radar/`) — the rest of the durable pipeline code, imported phase 3 (2026-07-24). REPOINTED to repo runtime (verified identical first): `pipelines/radar/corroborate.py`, `pipelines/build-space-search.py` (+ its template asset). Cloud-only fallback copies remain pending an independently verified repoint or retirement: `pipelines/build-search-map.py`, `pipelines/dso-match.py`, and the input-gated radar scripts (PECOS gate: October). Dell's migrated Mac reads this repo and does not use those Drive copies. Deliberately left out of the repo: the demoted Firefly receiver, deal-specific research one-offs, launchd plists.
- `shared/` — code mirrored to the shared DNA tier (lead-board template, vendor intro-path tool, fill-engine pair). The repo is canonical for both partner Macs; Drive copies remain only for cloud-only delivery/runtime until that surface is separately retired.
- `video/` — the video lane's code, imported 2026-07-25 (it had been living untracked at `~/Movies/CARR Video Pipeline/Scripts` since it was built on Jul 22). AE ExtendScript comp builders + their shell/python drivers: lower thirds, stock b-roll clips, the animated-static builder, the watch-folder encoder, the Premiere XML cutter, and the parked CEP panel. Mapped by `manifest-video.tsv`, whose runtime root is `~/Movies/CARR Video Pipeline` rather than the vault — the pipeline lives outside Drive on purpose because media files are large and Drive sync would churn on them, but the CODE still belongs under version control. Repo is the source of truth, `~/Movies` is the runtime. No output baselines for this lane (video outputs are large binaries and do not belong in git); review the generated video visually before accepting a change. Reasoning and the exclusion list are recorded in the manifest header.
  - Animated statics: `make-animated-static.sh <layers> <name> [--concept K] [--sfx N] [--email] [--dry-run]`, planned by `video/plan-animated-static.py` against `choreography-log.tsv` so a recent shape is never repeated (7 concepts, 5 landing sounds; one sound per piece, varying between pieces). `--email` adds an Outlook-safe cut: legacy Outlook on Windows renders only a GIF's first frame, so that cut leads with the FINISHED card for one frame and does not loop. Social dosing doctrine is in the doctrine store (two per month, never consecutive weeks, IG/FB only, never X); its former vault path was `Marketing/Social Media/social-media-workflow.md`.
- `bin/` — local automation utilities, calendar and mail capture, and backup tools.
- `baselines/` — committed snapshots of each generator's current output. Any code change must show its output diff against these before being accepted (the checkability guardrail: nobody reads this code line-by-line, so the outputs are what get reviewed). The PII-bearing outputs (`lead-board.html`, `deal-room-panhandle.html` since ORDER 42b 2026-08-06, `renewal-radar.json` since 2026-09-26) are hash-only tracked — see the Boundaries section.
- `tools/health-check.py` — the rule-28 façade check as code (phase 3 pilot): every watched pipeline output tested for STALE-past-cadence and BEHIND-its-inputs. `run.sh health`; the daily heartbeat's JOB 4a calls it.
- `tools/writing-lint.py` — the writing rules (now in the doctrine store; formerly `DNA/writing-rules.md`) as a deterministic gate (2026-07-25). `run.sh lint <file> [--surface email|social|proposal|web]`. HARD findings are hard bans with a per-instance signature (any hit fails, exit 1); REVIEW findings are hard-ban rules whose pattern has legitimate CRE uses (land, leverage-the-noun, landscape, shift) and always need a human; RATION covers the one-per-piece filler adverbs. It does NOT replace the `writing-audit` skill's judgment pass — it precedes it, the same way the ban list precedes the judgment checks inside writing-rules.md. Product examples are checked by `tools/test-writing-lint.py`.

## Execution model (phase 2 live, 2026-07-24)

**On both partner Macs this repo is the code runtime.** Joe's primary machine runs the scheduled pipelines; Dell's migrated secondary has no CARR AI vault mounted and runs no primary-only scheduled tasks. Normal `./run.sh deal-room|lead-board|all` paths use canonical record inputs and fail closed when they are unavailable. `renewal-feed` remains an explicit recovery-only MLS-file command until canonical MLS ingress exists; Drive use requires `--recovery --reason WHY` (and optional `--vault PATH`), while normal mode ignores ambient `CARR_VAULT`.

**The vault code copies are not a runtime on either partner Mac.** The rows left in `manifest.tsv` pair repo code with a cloud-only delivery fallback copy in Drive; those copies stay until that surface has a verified replacement or retirement, and are never doctrine. Change flow:

1. Edit the script here (branch + PR if you're Dell's side — see access model).
2. Run `ops/ci.sh --strict` and the affected product tests.
3. When a change is accepted: run the pipeline via `run.sh`, verify the output diff is exactly the intended change, update the baseline, copy the script to its vault path only if its manifest row still pairs it with a Drive fallback, and commit baseline + code together.

## Access model (Joe's decision, 2026-07-24)

- Joe: owner, merges everything. Joe is the code-savvy partner; every fix lands through him.
- Dell's side: authenticated access to the private repo is live. Dell works on a named branch, pushes that branch, and opens a PR; Joe reviews and merges. The installed pre-push hook refuses Dell's direct pushes to `main` and preserves the review gate.
- Platform-enforced branch protection on a private repo still requires the appropriate paid GitHub plan. Until then, the local pre-push hook plus Joe-owned merge is the active control.

## Boundaries

- This repo is PRIVATE and stays private. It is not, however, where client PII is meant to live: prospect/client PII never lives in GitHub (Joe's ruling (a), 2026-08-01; ORDER 42). The output baselines that used to carry it in full — `baselines/lead-board.html` (~60,687 emails / 218 phones) and `baselines/deal-room-panhandle.html` (91/49), moved under ORDER 42b (2026-08-06), and `baselines/renewal-radar.json` (~150 tenant/landlord names, addresses and phones), moved 2026-09-26 following the same pattern — are hash-only tracked: the full file stays LOCAL and gitignored, only its sha256 is committed (`baselines/SHA256SUMS`). `backups/*.sql.age` (encrypted full DB dumps) likewise moved out of git to the R2 archive under ORDER 42b; see `bin/backup-dump.sh` and `lib/r2_archive.py`. History predating each of these conversions may still carry the full file in older commits — the ORDER 42b purge removed `lead-board.html`/`deal-room-panhandle.html`/`backups/` from history; `renewal-radar.json`'s prior history has not been rewritten and is pending Joe's decision. The partner boundary is open (Joe and Dell share all client details); the external publication firewall is absolute.
- Generators write local files/artifacts only. Claude sends outbound messages when Joe asks, through his own accounts; with no ask, the default is a draft (decision 41906a30).

## Working-tree hygiene (2026-08-08)

Several sessions share this one checkout, so its state is shared state.

- **Claim before you branch.** `git checkout -b` moves HEAD for every session
  in this tree; commits then land on whichever branch someone else chose
  (rule 308ef1de). Prefer `git worktree add --detach <path> origin/main` for
  isolated work — it never moves shared HEAD, and two sessions can hold one
  each. A worktree cannot check out a branch another worktree holds, which is
  why detached is the reliable form.
- **Never commit build output or environments.** `.build/` (Swift) and
  `.venv*/` (Python) are gitignored as of today, after 274 Swift artifacts
  and one 155MB venv were committed by broad `git add` calls. Add paths
  deliberately; `git add <dir>` sweeps whatever is sitting in it.
- **Untracked is not saved.** Two files lived all day as untracked local
  copies with no commit on any ref, one `git clean -fd` from gone. If a file
  matters, commit it the day it is written.
