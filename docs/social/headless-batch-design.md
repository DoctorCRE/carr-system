# Headless Friday social batch: design (WR-000561, slice S1)

Status: S2 built, 2026-10-27 (America/Chicago). The script, cadence config, prompt and
launchd template exist but are not installed or scheduled: the plist label is
`DEFINITION_ONLY` and the prompt carries the disabled marker, so neither installer will
place them before S3. The current Claude Desktop task `social-batch-weekly` keeps running
unchanged until slice S3 proves the replacement (Joe, 2026-10-27).

S2 amended D4, D5 and acceptance 3 after the #2533 review. The orchestrator ruled on its
three guard findings, and the amended text below follows those rulings.

The goal is the same weekly review drafts in the same Blotato queue, made by a headless
run on the Studio. Fixed rules go into a tested script. The model only writes drafts and
cards.

## 1. Baseline: the bar the new run must meet

Sources read on 2026-10-27:

- The Desktop scheduler's run history for `social-batch-weekly`.
- The two run transcripts:
  - `~/.claude/projects/-Users-booko-carr-system/fa738e82-14d3-45f6-9a8a-8ce57488ed28.jsonl`
  - `~/.claude/projects/-Users-booko-carr-system/16aaff0c-1cff-41f2-8c35-50a6d9b88bf5.jsonl`

  Both were located through `~/Library/Application Support/Claude/claude-code-sessions/…/local_*.json`, whose `cliSessionId` points to the transcript.
- The store's batch records: `content-inspiration-bank` sections 4F and 4G.
- A read-only `GET /v2/posts` of the Blotato queue.

There is no run on 2026-10-17. The Friday 10/16 firing did not run on time. The scheduler
started it on Mon 10/19, and that run is the first one below. The 10/09 firing reported
"succeeded" after zero seconds and did nothing.

| | Run 1: 10/16 firing, started Mon 10/19 09:10 CT | Run 2: Fri 10/23 08:08 CT |
|---|---|---|
| Wall time | 14 min | 17 min |
| Target week | Tue 10/20 to Sun 10/25 (Monday left empty for review) | Mon 10/26 to Sun 11/1 |
| X | 7: 4 standalone (incl. Real Numbers + story-time) and 3 one-liners | 8: 3 standalone (incl. Real Numbers + story-time) and 5 one-liners |
| LinkedIn / Facebook / Instagram | 2 / 2 / 2 | 2 / 2 / 2 |
| Total review drafts | 13 | 14 |
| Cadence against the spec | In range (3–4 standalone, 3–7 one-liners, story-time 1–2, one visual, one series slot, no quote-tweets). CARR matched the prior week. | In range. The 4th standalone was left out on purpose: no usable fuel, so "schedule less". |
| Format rotation | 4 CARR formats, an IG carousel, X waffle chart | 4 CARR formats, an IG carousel, the first X animated GIF |
| Grok pass | Done, headless `grok -p` | Done, headless `grok -p` |
| Lint | 0 HARD. 1 REVIEW cleared, 2 REVIEW fixed. | 0 HARD at the end, after 3 HARD were fixed. REVIEW hits cleared. |
| Claims review | Independent Sonnet reviewer, findings applied | Same |
| Readback | Each post read back by submission id. Media byte-identical, 9/9. | Same, 9/9. DST handled by hand for 11/1. |
| Failures | Fired 3 days late. Joe was dark 10/12 to 10/19. 5 guard-hook refusals. | 12 guard-hook refusals on source downloads. One Browser-pane navigation declined. |

Today's queue (read 2026-10-27) holds all 14 Run 2 posts: 4 published and 10 scheduled
through 11/1. Of Run 1, 12 of 13 posts are still in the listing, all published. The 13th
(10/20) is now older than the listing reaches.

Repeated waste in both transcripts:

- Each run grepped the previous run's transcript to rediscover how to upload media and
  schedule posts.
- Each run rewrote its card generator from scratch.
- `claude-tree/skills/social-media-manager/scripts/blotato.sh` was not used, because it has no list call, no windowed read and no
  presigned upload. The runs called Blotato with raw curl instead.
- Both batch records flag the same drift. The task prompt still names a Chrome Grok
  pass and a vault batch file. The store retired both: the Grok pass on 08-07
  (rule 57d13061), and hand-written vault markdown by rule 14181e60.

Bar for S3: the same number of posts per platform within the spec's cadence, every post
read back as `scheduled` in the future, byte-identical media, 0 HARD lint hits, and no
double-scheduling.

## 2. Terms

These follow the doctrine's own wording. The glossary lives in the store; these are the
meanings this design uses.

- **Batch**: one run's set of drafts for one target week.
- **Target week**: Monday 00:00 to the next Monday 00:00 in America/Chicago, for the
  first Monday after the run date. It is never a UTC week (see the guard decision below).
- **Lane**: X standalone, X one-liner, or a CARR platform post (LinkedIn, Facebook,
  Instagram). Story-time and the recurring series are kinds of X standalone, not
  separate lanes. 4F and 4G both count them that way.
- **Slot**: one allowed post in a lane for the target week. **Open slots** are the
  spec's maximum minus what Blotato already holds in that week.
- **Format**: text, single card, split-stat, big-number, quote card, carousel, chart,
  animated card, and so on. Rotation rules apply to formats.
- **Review draft**: a Blotato post scheduled in the future that Joe reviews or cancels
  before it fires. Nothing is published immediately.
- **Guard**: the code check that refuses to add posts beyond the open slots.

## 3. Decisions (self-grilled, each with what would reopen it)

Each question was answered from the store, the transcripts, or the repo. Joe was not asked.

**D1. Runner: reuse `lib/headless_tasks.py` (`bin/headless-task`). Do not write a new launcher.**
It already provides:

- the launchd plist template and installer (`ops/headless-tasks/install.sh`);
- `claude -p` on the subscription login, with API-key environment variables stripped and API settings refused;
- `--permission-mode dontAsk` with an allowlist, a timeout and a run ledger;
- failure alerts, and a completion receipt checked against a typed `acceptance_contract`.

Platform features reused: launchd `StartCalendarInterval` (StartInterval never fires on
the Studio), and the existing headless manifest `ops/headless-tasks/tasks.json`.

Conflict noted: #2448 (2026-10-27 09:33 UTC) emptied the manifest and wrote "Custom
headless recurrences retired". That PR retired two tasks it could not verify, not the
runner. Joe's later go-ahead on WR-000561 the same day, with the WR's HEADLESS criterion,
is the live direction.

*Reopen if* the runner is deleted, or Joe rules that no new headless recurrences are allowed.

**D2. Seam: the model writes one drafts JSON file. The script owns everything the spec fixes.**
The script is a deep module with three commands behind `./run.sh social-batch`:

- `plan` (read-only): open slots, recent formats, DST offsets.
- `check <drafts.json>` (pure dry run): every rule below. Returns a list of findings.
- `submit <drafts.json>`: runs plan and check again against a fresh queue read, then
  uploads, schedules, reads back, and writes the logs.

`submit` refuses on any finding. The model never calls Blotato itself.
`submit` re-reads the queue because the model runs for about 15 minutes, and Joe or the
old task may change the queue in that time.

*Reopen if* the model needs a Blotato capability the script does not offer, such as
threads through `additionalPosts`. Add it to `submit`; do not hand the model the key.

**D3. Language and Blotato client: one Python module, `tools/social_batch.py`, with stdlib HTTP.**
`claude-tree/skills/social-media-manager/scripts/blotato.sh` stays unchanged for the social-media-manager skill. The module reads the key
the same way the credential inventory's probe does: it sources `~/.zprofile` in a child
shell and passes the key as a header on stdin. It never puts the key in argv and never
logs it.

*Reopen if* a second Python caller needs Blotato. Then extract the proposed `lib/blotato.py`.

**D4. Cadence numbers live in a small pinned config, checked against the store at every run.**
`ops/social/cadence.v1.json` holds the numbers below. It also holds the `section_id` and
`content_hash` of each governing section:

- x-posting-spec "Cadence, format, and visuals reset";
- social-media-workflow "Format rotation is MANDATORY".

`plan` reads both sections with `read-doctrine`. If either hash has changed, it stops
with `spec_changed`, and the batch does not run on stale numbers. Prose is never parsed
into rules. The store still wins, because a change there halts the run until someone
re-pins the config.

| Rule | Value | Source |
|---|---|---|
| X standalone | 3–4 per week, business hours | cadence reset |
| X one-liner | 3–7 per week, additive, off-pattern times, no hashtags, no CTA | task prompt (Joe 7/31), 4F/4G |
| X story-time | 1–2, counted inside standalones, must say it is invented | spec "story-time", 4F/4G |
| X visual | exactly 1, format not used in the last 4 X visuals, no CARR branding | cadence reset |
| X recurring series | exactly 1 | cadence reset |
| X every day | at least 1 X post on each of the 7 days | spec "Schedule into Blotato" |
| X quote-tweets | 0 | cadence reset (owned by x-reply-run-daily) |
| LinkedIn / Facebook / Instagram | 2 / 2 / 2 per week, pinned; never derived from last week's count | both baseline runs; #2533 review ruling |
| CARR formats | at least 3 distinct, a carousel on at least 1 platform, no platform all single cards | workflow format rotation |
| Animated card | at most 1 per batch, not on X, not in two weeks running | workflow animated statics |
| Instagram hashtags | at most 5 | task prompt |
| Times | minute not divisible by 5, no X time repeated on two days | spec "vary the times", 4F/4G practice |

*Reopen if* `spec_changed` fires more than once a month. That would mean the cadence
belongs in a typed store record, not a pinned hash.

**D5. Guard: fail closed, and window by the America/Chicago week.**
`plan` reads `GET /v2/posts?since=…&until=…&limit=100`, using the local week converted to
UTC, and filters the answer to that local week again itself. It also reads back, by
submission id, every post its own batch ledgers (`out/social-batch/*/ledger.jsonl`) record
for the target week, deduplicated by platform and minute. A post X does not attribute to a
lane counts against both X lanes. The previous week is read as a control: an empty answer
there warns `control_week_empty` and never blocks, so one missed Friday cannot lock out
every later run.

Listing-horizon probe, read-only GETs on 2026-10-27 19:07 UTC: the unwindowed
`GET /v2/posts` returned posts back to 10/21 00:52 UTC (about 6 days 18 hours) plus every
upcoming post. That is the "7 days" in the old prompt. A windowed read of 10/19–10/26
returned the 10/20 post the default listing had dropped. Forward reach could not be shown
live, because nothing was scheduled past +5 days. The 10/23 run transcript shows its
windowed read returning a post 9.5 days out (11/2 00:39 UTC). The by-id readback covers
the remaining doubt for posts this script made.

Evidence for the local-time rule: on 2026-10-27, the UTC window for next week
(11/02–11/09) returns last week's Sunday 18:39 CST one-liner (00:39 UTC on 11/2). A UTC
window would count it as next week's post.

Outcomes:

- If Blotato cannot be read, answers with an error, or answers in a shape other than the recorded one, the run stops with `queue_unreadable` and schedules nothing.
- If the target week already meets every lane maximum, the run ends `noop` with `week_covered`.
- Otherwise, open slots are each lane's maximum minus what the week already holds.

The fixture test the WR asks for runs here: a recorded queue that already covers the
week must give zero open slots, and `submit` must refuse a full batch against it.

*Reopen if* Blotato's listing changes shape, or a post is missing from both the window
and the submission-id readback.

**D6. Lint gate: run `tools/writing-lint.py --surface social` on every body and every card's text.**
Any HARD hit refuses the draft. A REVIEW hit needs a matching
`lint_review_clearances[]` entry with a reason; otherwise it refuses.

The model runs `check` itself until it is clean, so the fix-and-relint loop stays with
the model. `submit` re-runs the lint, so a stale check result cannot get through.

*Reopen if* clearances turn into rubber stamps. The sign would be the same REVIEW rule
cleared in more than half of the posts across three batches.

**D7. Publishing gate: only the script can make a write, and every write is a future review draft.**
`submit` refuses any post that:

- is scheduled before the target week's Monday 00:00 CT, or less than 12 hours after the run;
- is missing a `scheduledTime`;
- targets an account not in `claude-tree/skills/social-media-manager/config/blotato-accounts.json`.

The script has no code path for replies, likes, follows, edits or deletes. Media go
through `POST /v2/media/uploads` and a PUT, then the file is downloaded again and its
SHA-256 matched. Each post is then read back by submission id and must show
`status: scheduled` at the intended minute. An idempotency ledger keyed by draft id
means a retry after a crash cannot post twice.

*Reopen if* Blotato adds a real "draft, not scheduled" state. That would be a stronger gate.

**D8. Logs: the script writes the facts, and the model writes the judgement.**
`submit` does three things:

- It writes `out/social-batch/<week>/batch.json`: drafts, submission ids, readback, media hashes, lint results.
- It adds the next "4X" section to `content-inspiration-bank` through `./run.sh call write-doctrine-section`. Sections are separate because section 4 has no append operation. The section is rendered from the facts plus the model's `run_notes`: Grok findings, what improved, what did not, angles used, corrections to the bank.
- It opens the review loop through `add-loop`, listing each post's fire time. This is the scheduled-post enforcement rule.

No vault markdown and no `x-batch-*.md` file are written, which matches the store over the task prompt.

*Reopen if* the store gains an append verb for section 4.

**D9. Grok: run the headless `grok -p` CLI. Chrome is not used.**
The store amended the spec on 2026-08-07 (rule 57d13061): the pass uses the Grok Build
CLI on every surface. Both baseline runs already ran it headless. The Chrome wording in
the task prompt is drift.

If `grok` is missing, unauthenticated, or past its time limit, the run goes ahead from
the banks. It records `grok.status: skipped` with the reason, and `check` refuses any
draft whose `sources` are marked `grok_only`. A failed leg is always named.

Account-scoped surfaces (X Radar, Creator Analytics) stay out of this batch, as they are today.

*Reopen if* a batch skips Grok two weeks running.

**D10. Source verification without a browser.**
The baseline used the Desktop Browser pane for pages that block curl (bls.gov, The Real
Deal), and the guard hook refused 17 curl downloads that carried header flags. A headless
run has no Browser pane.

The prompt therefore names `WebFetch` as the first route and a plain `curl -q` with no
headers as the second. Any figure that cannot be read at its primary source is dropped
or the post is left out: "schedule less" (the spec's quality bar). Each draft's
`sources[]` records the route used.

*Reopen if* the S3 run drops more than one post because a source was unreachable. Then
consider a Chrome route on the Studio (`claude-in-chrome`) as an optional extra.

**D11. Cards and the vision check stay model work, on headless Chrome and ffmpeg.**
`chrome --headless --screenshot` and ffmpeg are local binaries and need no logged-in
Chrome. The model writes card HTML using the brand fonts and opens the PNG with Read to
do the vision check. `check` only confirms that each media file exists, has the right
dimensions and type for its platform, and that its text was linted. The prompt points
the model at a tracked card template folder, so it stops rewriting the generator every
week.

*Reopen if* card defects reach Joe twice. Then rendering moves into the script, and the
vision check becomes a required `check` input.

**D12. Model, tools and schedule.**

- Model: `--model opus`. Both baseline runs used Opus at medium effort, and the runner's
  default is Sonnet. This is the subscription, not the paid API.
- Allowlist (the #2538 review narrowed it so the model cannot rewrite its own guard or
  reach any other writer):
  - Read, Glob, Grep, WebFetch, WebSearch;
  - `Agent`, which keeps the independent Sonnet claims reviewer that caught real errors both weeks;
  - Write and Edit only under `out/social-batch/*/work/**`;
  - `Bash(./run.sh social-batch *)` and `Bash(./run.sh call read-doctrine *)`.

  The deny list covers `tools/`, `lib/`, `ops/`, `bin/`, `run.sh`, the batch ledger and
  `out/social-batch/<week>/batch.json`, the Blotato hosts, `~/.zprofile`, curl and wget. `submit` calls the store
  write verbs from its own subprocess, so the model needs no write verb.
- Headless Chrome runs only through `./run.sh social-batch render`. Both the card HTML and
  the PNG must sit in that work folder. Render serves the folder from a throwaway
  `127.0.0.1` server as `http://card.test/`, and the same server is Chrome's only proxy
  (`--proxy-bypass-list=<-loopback>`, with `--host-resolver-rules` mapping every name to
  NOTFOUND). It answers GET for that origin only: the work folder plus the tracked brand
  fonts at `/fonts/`. So a card cannot frame `file://` URLs, other local files, other local
  services or outside hosts, and cannot leak them into a PNG the model reads (#2538 round 2).
  Render also uses a throwaway profile and `--use-mock-keychain`, which avoids the keychain
  prompts that hung securityd on 2026-10-24, and keeps the Chrome binary off the allowlist.
- Two capabilities are left out until S3:
  - **ffmpeg and the animated-static builder.** A permission rule cannot bind their output
    paths. Until S3 the batch ships static cards only.
  - **`grok -p`.** It is an agent with its own tools, and Claude's deny rules do not bind
    it. Its `--tools`, `--sandbox` and `--permission-mode` flags exist, but the built-in
    tool names were not confirmed: the 2026-10-27 probe returned HTTP 402 "usage balance
    exhausted". **S3 precondition:** confirm a read-only grok invocation, then add exactly
    that pinned command to the allowlist. Until then the prompt records `grok.status:
    skipped` (D9's fallback).
- Schedule: Friday 06:00 CT (`0 6 * * 5`), with a 5400 s timeout and a 3600 s grace.
- Task id: `social-batch-headless`, kept separate from `social-batch-weekly` so the old
  file is never touched.

The prompt file lives at `~/.claude/scheduled-tasks/social-batch-headless/SKILL.md`, with
a tracked copy at `ops/scheduled-tasks/social-batch-headless.SKILL.md` so config-as-code
reconciles it. The prompt is one JSON object in a ```json fence, because
the `contract` function in `lib/acceptance_checks.py` reads the acceptance
contract only from a whole-JSON prompt. A markdown prompt yields no criteria, so its run can never be accepted. Model,
allowed tools and the deny list (Blotato hosts, `~/.zprofile`, curl, wget) live in the
task's `ops/headless-tasks/tasks.json` entry. A SKILL.md alone does not register a Desktop task:
`deal-history-research-weekly` has one there and is absent from the scheduler list. No
sidebar session is created.

*Reopen if* the prompt directory ever starts registering Desktop tasks on its own.

**D13. Running alongside the old task: the old task is the fallback.**
The headless job fires at 06:00, before the old task's 08:08. The old prompt already
reads Blotato first and never double-schedules a covered week. So in a week where
headless succeeds, the old task finds the week covered and stops. In a week where
headless fails, the old task fills the week as it does today.

Nothing about the old task is disabled or edited.

*Reopen if* the old task tops up a week the headless run had filled. That is the risk of
a partial headless failure, and S3 checks for it explicitly.

**D14. Completion evidence.**
`submit` writes a summary next to the runner's receipt (`$CARR_HEADLESS_RECEIPT.artifact`):

- `SOCIAL_BATCH_SCHEDULED n=<k> verified=<k>` when every post read back;
- `SOCIAL_BATCH_WEEK_COVERED` for a no-op.

The prompt's `acceptance_contract` uses `contains` criteria on that path, with a
`noop_criteria` branch. Known limit: the model has the Write tool and could forge the
file. S3's acceptance therefore reads Blotato independently and never trusts the
receipt alone.

*Reopen if* the runner gains a way to pin an artifact's writer.

## 4. The step split

| Current prompt step | Kind | Where |
|---|---|---|
| Orient: read INDEX.md and workflow files on Drive | dropped | the prompt reads store docs by slug |
| Fuel sweep: substance bank §2, feedback log §4, fuel inbox | model | `read-doctrine content-inspiration-bank`. The inbox still sits only on Drive and is read there. |
| Read what Blotato already holds; no double-scheduling | fixed | `plan` and the D5 guard |
| Research sources (`list-research-sites`, open web) | model | unchanged; D10 routes |
| Grok pass | model | `grok -p` (D9) |
| X lane drafting, voice, style contract | model | drafts JSON |
| X cadence, visual rotation, no quote-tweets | fixed | `check` (D4) |
| CARR platform drafting | model | drafts JSON |
| CARR cadence, format rotation, IG hashtag cap | fixed | `check` (D4) |
| Card rendering and vision check | model | D11 |
| Media upload and scheduling | fixed | `submit` (D7) |
| Lint every body | fixed (the gate), model (the fixes) | `check` and `submit` (D6) |
| Claims review | model | Agent subagent |
| Batch log, feedback log, review loop | fixed (the facts), model (`run_notes`) | `submit` (D8) |

## 5. The JSON contract

The model writes `out/social-batch/<week>/drafts.json`. Unknown fields are refused.

```json
{
  "schema": "carr-social-batch-drafts/v1",
  "week_start": "2026-11-02",
  "plan_digest": "sha256 of the plan output this batch was drafted against",
  "drafts": [
    {
      "id": "x1",
      "platform": "twitter | linkedin | facebook | instagram",
      "lane": "standalone | one_liner | carr",
      "kind": "take | series | story_time | null",
      "format": "text | single_card | split_stat | big_number | quote_card | carousel | chart | animated_card | ...",
      "local_time": "2026-11-03T10:23",
      "text": "post body exactly as it will publish",
      "media": [{"path": "/abs/path/x1.png", "card_text": "every word on the card"}],
      "visual": true,
      "sources": [{"url": "https://...", "route": "webfetch | curl | grok_only | bank", "claim": "the figure used"}],
      "lint_review_clearances": [{"rule": "rule id", "reason": "why this hit is fine"}],
      "bank_angles": ["bank entry and angle used"]
    }
  ],
  "run_notes": {
    "grok": {"status": "done | skipped", "reason": "", "findings": []},
    "claims_review": {"done": true, "applied": [], "declined": []},
    "improved": [],
    "not_improved": [],
    "skipped_slots": [{"lane": "standalone", "reason": ""}],
    "bank_corrections": []
  }
}
```

`check` and `submit` print one JSON object: `{"ok": bool, "findings": [{"draft": id,
"rule": id, "detail": text}], ...}`. `plan` prints the open slots per lane, the min and
max per lane, recent X visual formats, the prior week's CARR counts, each day's UTC
offset, and `plan_digest`.

## 6. The trimmed, store-first prompt (outline for S2)

At most about 40 lines, holding no rules the script enforces:

1. Hard gate, one paragraph: review drafts only, never publish, never touch replies,
   likes or follows. Only `./run.sh social-batch submit` writes.
2. Run `./run.sh social-batch plan`. If it reports `week_covered`, write the no-op and
   stop. If it reports `queue_unreadable` or `spec_changed`, stop and say which.
3. Read the store by slug: `x-posting-spec`, `social-media-workflow`,
   `content-inspiration-bank` (§2 and the newest §4 sections), `x-strategy-2026`,
   `brand-voice`, and the voice and style documents the spec names. Read Drive only for
   files that have no store copy (today: `{{VAULT}}/Marketing/Social Media/content-fuel-inbox.md`), and name each one.
4. Research and Grok per D9 and D10. Verify every figure at its primary source.
5. Draft to fill the open slots from `plan`, never past them. Render cards and look at
   each PNG.
6. Run the claims reviewer subagent, then `./run.sh social-batch check` until it
   returns no findings.
7. Run `./run.sh social-batch submit`. Report its summary line.
8. `acceptance_contract` JSON (D14).

## 7. Slices

**S2: build (one PR, TDD).**
Adds:

- `tools/social_batch.py` (`plan`, `check`, `submit`) and its `run.sh` verb;
- `ops/social/cadence.v1.json`;
- a test file with recorded Blotato fixtures;
- the tracked prompt;
- `ops/headless-tasks/com.carr.headless.social-batch-headless.plist` and its `ops/headless-tasks/tasks.json` entry.

Acceptance:

1. A fixture where the week is already covered gives 0 open slots, and `submit` refuses a full batch (the WR's DETERMINISTIC criterion).
2. A fixture that crosses the DST change on Sun 11/1 places each post at the right UTC minute, and a UTC-edge post is counted in the right local week.
3. An unreadable or malformed listing fails closed. An empty control week warns and plans from the spec cadence (amended by the #2533 review ruling).
4. A draft with a HARD lint hit, an uncleared REVIEW hit, a 6th IG hashtag, a quote-tweet, or a past or too-early time is each refused by `check`.
5. A changed spec hash stops `plan` with `spec_changed`.
6. A retried `submit` after a simulated crash does not post twice.
7. The tests make no network calls.
8. `ops/headless-task-selftest.py` and `config-as-code check` pass with the new task.
9. The prompt names no Drive path that has a store copy (STORE-FIRST), checked by a test that greps the tracked prompt.
10. Not installed on the Studio until S3.

**S3: one real Friday, then retire the old task.**

1. Install with `ops/headless-tasks/install.sh install social-batch-headless`.
2. Let it fire Friday 06:00 CT.
3. Check, from an independent `GET /v2/posts` read:
   - every lane is within D4's ranges and the totals match the baseline (X 7–8, LinkedIn 2, Facebook 2, Instagram 2, or fewer with a stated reason);
   - every post is `scheduled` in the target week and none is published immediately;
   - media are byte-identical;
   - the 4X section and the review loop exist;
   - the run ledger shows success;
   - no sidebar session was opened.
4. At 08:08 the old task must find the week covered and add nothing. Check the queue
   count after it ends.
5. Only after that, and with Joe's yes, disable `social-batch-weekly`. That is the only
   change ever made to it. Then close WR-000561 with `complete-work-request`.
6. If the headless run fails, the old task fills the week. Then fix the failure and repeat S3.

## 8. Out of scope, noted

- Dell's batch (`ops/scheduled-tasks/dell/dell-social-batch-weekly.SKILL.md`) could later
  reuse `tools/social_batch.py` by taking an account set. Not in this WR.
- `ops/config/credential-inventory.v1.json` names the old script `lib/system_sender.py` as a Blotato
  user, but that file does not exist. This is a stale pointer, unrelated to this design.
