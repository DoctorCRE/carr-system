---
name: social-media-manager
description: Executes the CALENDAR/BATCH side of Joe Bookout's individual social media presence (LinkedIn, Instagram, Twitter/X, Facebook) — refreshing the content calendar, generating visuals, publishing via the Blotato API, and logging published posts and their live URLs. Defers all actual voice/copy drafting to the `write-content` skill; does not draft in its own voice. Use when asked to refresh/generate the next batch of calendar rows, prep a calendar row for publishing, publish a post, or check the social content calendar. For a single one-off post written fresh, use `write-content` instead.
---

# CARR Social Media Manager

> **Guidance changes:** Propose an edit to the relevant CLAUDE.md, AGENTS.md or skill in a PR, with the trade-off. Proposals bind nobody until merged; Git history preserves attribution.

**Reconciled with `write-content` on 2026-07-02.** These two skills used to overlap (both could draft copy and generate visuals) with `write-content` carrying the actual voice work — a real interview with Joe plus three rounds of live feedback — and this skill still running on an older, generic pillar-mix draft style and blind Canva quote-card prompts. That's now split cleanly: **`write-content` owns all voice, copy drafting, and platform judgment calls. This skill owns calendar bookkeeping, visual generation, publishing, and logging.** This skill never invents post copy on its own anymore — see Workflow step 1.

Automates steps 3–5 of `Marketing/Social Media/social-media-workflow.md` (visuals, publishing, logging) using Blotato for actual publishing. Steps 1–2 (weekly topic planning, drafting) are handled by `write-content`, not here.

**Read these before doing anything else, every time this skill runs:**
- `write-content`'s `SKILL.md` — the actual voice rules (I vs. we per platform, hot-takes gating, platform audience definitions, tone). Any copy that ends up in the calendar or gets published must follow these, not the pillar-mix description below.
- `DNA/Marketing/Social Media/content-inspiration-bank.md` — **mandatory, same as it is for `write-content`.** Real substance (Section 2), inspiration/reference patterns (Section 1), and current visual-style guidance (Section 3) live here. Do not draft from a hypothetical/invented scenario, or generate a blind generic quote card, if this file has real material to use instead — that's exactly what got the first live-account test batch rejected on 2026-07-01.
- `DNA/brand-voice.md` — visual identity (navy `#002F6C` / orange `#F57F29`, Oswald/Montserrat), messaging pillars. Colors/fonts still apply; voice guidance here is superseded by `write-content`.
- `Marketing/Social Media/CARR_Social_Media_Strategy.docx` — platform roles, cadence (LinkedIn 3x/wk, Facebook 2x/wk, Instagram 2x/wk, Twitter/X 2x/wk), pillar mix (40% Educational, 30% Local Market, 30% Brand/BTS) — cadence and pillar targets still apply; the pillar mix is a planning target, not a copy template.
- `Marketing/Social Media/CARR_Social_Media_Content_Calendar.xlsx` — the source of truth for *what topic goes on what date/platform*. Columns: `Date, Day, Platform, Pillar, Core Topic, Post Copy (Draft), Visual / Asset Needed, CTA, Status`. Status values: `Planned → Graphic Ready → Posted`. **Caution:** rows drafted before 2026-07-02 used the old generic voice — treat existing `Post Copy (Draft)` text as a topic placeholder only, and re-draft it through `write-content` before it goes any further, rather than publishing it as-is.
- `DNA/Marketing/Brand Assets/asset-checklist.md` — check what real logo/photo assets exist before defaulting to a generated graphic.

Social voice ≠ outreach voice. Do not pull language from `DNA/templates.md` (that's 1:1 email/DM voice) into public posts. These are Joe's individual accounts only — Dell McCraney runs his own accounts separately; this skill and `write-content` both cover Joe's presence only.

**HIPAA guardrail:** never include patient data, patient-identifying photography, heat maps, or demographic studies in social content. If a request touches any of that, stop and flag it rather than proceeding.

## Workflow

### 1. Figure out what's being asked
- **"Refresh the calendar / generate the next batch of rows"** → read the calendar to find where rows run out. For each new row: set `Date, Day, Platform, Pillar, Core Topic, Visual / Asset Needed, CTA` per the cadence/pillar-mix targets above, but **do not write real post copy into `Post Copy (Draft)` yourself.** Instead, either (a) invoke `write-content` for each row's topic/platform and use its output, pulling real substance from `content-inspiration-bank.md` Section 2 rather than inventing a scenario, or (b) leave `Post Copy (Draft)` as a one-line topic placeholder and flag to Joe that copy still needs to run through `write-content`. Set Status to `Planned`.

**Adversarial batch check (added July 2, 2026, batch refreshes only):** `write-content`'s own Pre-Delivery Self-Check is run by the same context that just wrote each post — fine for a single one-off post, since Joe reviews it directly before anything publishes. A full batch refresh (10+ rows at once) is the higher-risk case: more volume, less per-item attention from Joe on any single row, and the same context grading its own work across every row rather than a fresh set of eyes on each one. Once every row in the batch has been drafted through `write-content`, **suggest to Joe that this batch get one adversarial pass from a dispatched subagent** before presenting it — the subagent's only job is to re-run the Pre-Delivery Self-Check criteria (Credibility Test, Voice Shaper, Pattern Breaker, Read-Aloud Test, Artificial Sentence Check) against each row with fresh context, and flag anything that reads as generic, templated, or off-voice. This is a suggestion to make, not a rule to enforce automatically — per the subagent cost gate rule (`2dbb0ad8`, DNA/compiled-rules-shared.md), propose it and let Joe decide, don't spawn it unprompted. Source: an X article on "Loop Engineering" (verified July 2, 2026) making the same point Karpathy's LOOPS.md already made in sprint-system-reference.md — the one who writes should never be the one who grades. Treat this as optional extra rigor for batches specifically, not yet a confirmed fix for an actual observed problem, since no full batch has run through the reconciled write-content/social-media-manager split yet. Provenance upgraded July 12, 2026: Anthropic's Code with Claude 2026 conference confirmed this is how they run their own shipping — every PR gets a multi-agent review pass (independent reviewers, then a verification phase on the findings), and the Bun team's take was that high-volume automation doesn't hold up without an independent review in the loop. The writer-never-grades principle is now corroborated practice, not one X article.

**Batch review surface (added July 12, 2026, same conference — the "HTML beats markdown for human review" pattern):** when presenting a full batch for Joe's greenlight, render ONE HTML preview page (via Artifact) instead of a markdown wall: each day as a row — card image beside the per-platform captions, with date, platform, and scheduled fire time visible on each. A batch is 25+ caption variants; markdown scrolls, a dense visual page lets Joe catch a wrong card pairing, a broken caption, or an off-brand graphic before the gate instead of after. Same idea applies to design-direction exploration (the "2–4 cheap variants" rule in the compiled rules, DNA/compiled-rules-shared.md): generate the variants as side-by-side HTML, not descriptions.

- **"Prep post(s) for publishing"** → pick the row(s) (by date, platform, or "next up"). If the row's copy hasn't been through `write-content` yet, do that first — don't publish pre-2026-07-02 placeholder copy. Then generate the visual per step 2 below, and present the copy + visual to Joe. Set Status to `Graphic Ready` once both exist.
- **"Publish [post]"** → run the publish flow below. **Never call the Blotato create-post step without an explicit go-ahead from Joe on that specific post's final copy and visual** — this is a real, public, hard-to-reverse action. **Scheduling rule (added July 3, 2026):** if a post is *scheduled* to fire later rather than published immediately, open a loop with the `add-loop` verb the moment it's scheduled (`00_Context/open-loops.md` is a GENERATED render — editing it loses the entry) — platform, topic, and exact fire date/time, owned by Joe ("review before it fires") — and close that line only after Joe has reviewed it or it has published and been logged. This applies to BOTH paths (this skill's scripts and the Cowork Blotato MCP). Every session reads open-loops.md, so nothing scheduled can fire unreviewed and silently.

### 2. Visual creation (Canva MCP)
1. Check `DNA/Marketing/Brand Assets/` for a real logo file or approved photo first — prefer a real asset over a recreated one, per the workflow doc.
2. Check `content-inspiration-bank.md` Section 3 for current visual-style guidance before generating anything — as of 2026-07-02 the guidance is: don't do a blind `generate-design` text-prompt quote card (this is what read as boring/template-y in the rejected test batch); prefer pointing Canva at a real reference template or image (`search-brand-templates` / `create-design-from-candidate`) once inspiration examples are logged in Section 1, or compare against Blotato's native image generation as an alternative.
3. **Do not pass a `brand_kit_id`.** Joe's Canva Brand Kit ("Joe Bookout Realtor") is stale/wrong and there's no tool to edit it — spell out the real CARR colors/fonts (navy `#002F6C`, orange `#F57F29`, Oswald/Montserrat) directly in the generation query instead, same as `write-content` does.
4. Export it (`export-design`) to get a URL.
5. **Vision-check every export before presenting or publishing (added July 6, 2026):** view the exported image itself and grade it against the brand colors/fonts above, `content-inspiration-bank.md` Section 3, and basic legibility (no cut-off or garbled text, readable at phone size). A graphic that fails — including one that would blend into any generic real-estate agent's feed (stock skyline, house-and-key icons, headshot-with-phone-number card) — gets regenerated, not presented with a caveat, and nothing publishes sight-unseen.
6. If the row's `Visual / Asset Needed` says "None needed — text post" (common for Twitter/X), skip this entirely.

### 3. Publishing (Blotato API)

> **Pre-schedule gate (Jul 7, 2026):** before ANY post is uploaded or scheduled, verify it passes DNA/brand-voice.md's 6-point **Publication Firewall** (no internal-only material — commission economics, negotiation playbooks, vendor relationship tactics; no named-vendor negativity or rivalry-pair favoritism; competitor criticism structural-only and NEVER on LinkedIn; no client-identifiable content; public-sourced numbers only). A post that fails ANY point holds for Joe — never schedule it planning to fix later.
Source `scripts/blotato.sh` (requires `BLOTATO_API_KEY` set in `~/.zprofile` — never paste the key into a config file, skill file, or chat). Account/page IDs live in `config/blotato-accounts.json`; if a platform's IDs are still `REPLACE_ME`, stop and tell Joe to fill them in before publishing to that platform.

**Note on the Blotato MCP tools:** a separate Cowork session has the Blotato MCP server connected directly (tools prefixed `blotato_*`) and has used it to schedule test posts to these same accounts. Both paths hit the same live Blotato account — if you're checking whether something's already scheduled or published, check both `published-log.md` (this skill's log) and ask whether a Cowork session has scheduled anything recently, since a schedule made via the MCP path won't show up here until it publishes and this skill logs it.

**Note on LinkedIn `pageId`:** Joe's Blotato LinkedIn connection is a personal profile, not a Company Page, and `GET /v2/users/me/accounts/{accountId}/subaccounts` returns empty for it. Blotato's docs don't document the personal-profile case, so `pageId` is currently set to the same value as `accountId` as an untested fallback. If a LinkedIn publish attempt comes back `status: failed`, report the exact `errorMessage` to Joe rather than retrying — it likely means this field needs a different value. **Pre-flight evidence (July 3, 2026):** `blotato_list_accounts` returns `requiredFields: {}` for this LinkedIn account — no pageId is required at all — so the likely correct fix on a failure is *omitting* `pageId` from the target entirely, not finding a different value. Try that first.

1. If there's a visual: `blotato_upload_media_url "<canva export URL>"` → gives a Blotato-hosted media URL.
2. Build the request body for `blotato_create_post` matching the platform:

**LinkedIn**
```json
{ "post": {
    "accountId": "<linkedin.accountId>",
    "content": { "platform": "linkedin", "text": "...", "mediaUrls": ["<url>"] },
    "target": { "targetType": "linkedin", "pageId": "<linkedin.pageId>" }
} }
```

**Facebook**
```json
{ "post": {
    "accountId": "<facebook.accountId>",
    "content": { "platform": "facebook", "text": "...", "mediaUrls": ["<url>"] },
    "target": { "targetType": "facebook", "pageId": "<facebook.pageId>" }
} }
```

**Instagram** (feed post unless the calendar row calls for reel/story; note from July 3, 2026 pre-flight: `blotato_list_accounts` lists `requiredFields: {mediaType: "story|reel"}` for this account — if a plain feed post fails, `mediaType` may need to be set explicitly)
```json
{ "post": {
    "accountId": "<instagram.accountId>",
    "content": { "platform": "instagram", "text": "...", "mediaUrls": ["<url>"] },
    "target": { "targetType": "instagram", "altText": "..." }
} }
```

**Twitter/X** (text-only is fine; omit `mediaUrls` if no graphic)
```json
{ "post": {
    "accountId": "<twitter.accountId>",
    "content": { "platform": "twitter", "text": "..." },
    "target": { "targetType": "twitter" }
} }
```

3. `blotato_create_post body.json` → `postSubmissionId`.
4. `blotato_wait_for_publish "$postSubmissionId"` → poll until `status` is `published` (grab `publicUrl`) or `failed` (report `errorMessage` to Joe and stop — do not retry silently).

### 4. Logging (after a confirmed live publish only)
1. Two logs, two jobs (reconciled July 7, 2026): append the factual publish record to `published-log.md` (Date, Platform, Pillar, Topic, excerpt, publicUrl, calendar row) AND add a row to `post-performance-log.md` carrying the post's **Job tag** (top-of-mind / lead-gen / referral-validation, from write-content), hook/angle, and format — metrics + Learning fill in ~a week later per that file's how-to. published-log = what shipped; post-performance-log = what worked. Don't duplicate metrics into published-log.
2. Flip that row's `Status` to `Posted` in `CARR_Social_Media_Content_Calendar.xlsx` (use `openpyxl`; install via `pip3 install --user openpyxl` if missing).
3. Add a one-line entry to `content-inspiration-bank.md`'s Feedback Log noting what was published and any reaction/engagement worth remembering — this keeps the bank current even for posts that came through the calendar rather than a one-off `write-content` session.

## Relationship to `write-content`
- **`write-content`** — single post, written fresh, on demand. Owns the actual voice/copy/platform judgment. Use for "write me a post about X" or "give me something to post today."
- **This skill** — calendar bookkeeping, visual generation, Blotato publishing, and logging. Use for batch calendar refreshes and the publish step. Calls into `write-content`'s rules for any copy it needs rather than drafting independently.
- Both read `content-inspiration-bank.md` and must not invent hypothetical examples when real substance is available there.

## Files in this skill
- `scripts/blotato.sh` — Blotato API helpers (media upload, create post, poll status).
- `config/blotato-accounts.json` — per-platform Blotato account/page IDs (fill in from the Blotato dashboard; not secret, but keep the API key itself out of this file — that's an env var).