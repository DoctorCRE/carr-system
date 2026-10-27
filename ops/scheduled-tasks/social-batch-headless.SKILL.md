```json
{
  "name": "social-batch-headless",
  "description": "Friday 06:00 CT headless social batch for Joe's X, LinkedIn, Facebook and Instagram: drafts and cards for the next Monday to Sunday, scheduled in Blotato as future review drafts by ./run.sh social-batch submit. Never publishes.",
  "activation": "This definition is disabled until WR561.S3: it is not installed, and config-as-code does not render it. S3 deletes this field, installs it with ops/headless-tasks/install.sh, and proves one Friday.",
  "design": "docs/social/headless-batch-design.md",
  "instructions": [
    "HARD GATE. You write drafts and cards only. Only `./run.sh social-batch submit` writes to Blotato, and every post it makes is a future review draft Joe can cancel. Never publish, reply, like, follow, quote-tweet or call Blotato yourself.",
    "1. Run `./run.sh social-batch plan`. It prints the target week (Central), open slots per lane, held posts, recent X visual formats, warnings and plan_digest.",
    "   - status week_covered: the week is full. It wrote the no-op artifact; write the noop receipt and stop.",
    "   - status queue_unreadable, spec_changed, spec_unreadable or error: stop, say which and its detail, and write no receipt.",
    "   - Name every warning in your final report.",
    "2. Read the store by slug with `./run.sh call read-doctrine '{\"document\":\"<slug>\"}'`: x-posting-spec, social-media-workflow, content-inspiration-bank (section 2 and the newest section 4 entries), x-strategy-2026, brand-voice, and every voice or style document those name. The store wins over any file.",
    "3. Read the one fuel file with no store copy: {{VAULT}}/Marketing/Social Media/content-fuel-inbox.md. Joe's own lines go first. Say if it was unreadable.",
    "4. Grok pass: `grok -p` on what is trending in Joe's lanes. Grok spots trends; it is never the source of a figure. If grok fails, record grok.status skipped with the reason and draft from the banks.",
    "5. Verify every figure at its primary source with WebFetch. If a source cannot be read, drop the figure or the post. A thin week Joe tops up beats a wrong batch.",
    "6. Draft into the open slots from plan, never past them. Follow the specs for voice, cadence, format rotation and the quality bar. Pick Central wall-clock times; the script converts them to UTC.",
    "7. Cards: write HTML with the brand fonts, then `./run.sh social-batch render <card.html> <card.png> --size WIDTHxHEIGHT`. Open every PNG with Read and look at it before you use it. Animated cards use ./video/make-animated-static.sh; GIFs use ffmpeg.",
    "8. Write out/social-batch/<week_start>/drafts.json in the carr-social-batch-drafts/v1 shape (design doc section 5, plus alt_text on Instagram posts with media). Copy plan_digest from step 1. Record skipped slots with a reason.",
    "9. Run an independent claims reviewer with the Agent tool (model sonnet) over every post and card. Apply its corrections, then set run_notes.claims_review.",
    "10. Run `./run.sh social-batch check out/social-batch/<week_start>/drafts.json`. Fix every finding and run it again until it prints no findings. A REVIEW lint hit you keep needs a lint_review_clearances entry with a real reason.",
    "11. Run `./run.sh social-batch submit out/social-batch/<week_start>/drafts.json`. It re-plans, re-checks, uploads, schedules, reads back, writes the batch log, the feedback-log section and the review loop, then writes the artifact next to the receipt. If it refuses or stops, report why and write no receipt.",
    "12. Report: posts per platform with Central times, what Grok surfaced, how this batch improves on the last one, skipped slots, and the submit summary line."
  ],
  "acceptance_contract": {
    "criteria": [
      {"id": "scheduled", "kind": "contains", "path": "{receipt}.artifact", "text": "SOCIAL_BATCH_SCHEDULED"},
      {"id": "verified", "kind": "json_equals", "path": "{receipt}.artifact", "pointer": "/all_verified", "value": true}
    ],
    "noop_criteria": [
      {"id": "covered", "kind": "contains", "path": "{receipt}.artifact", "text": "SOCIAL_BATCH_WEEK_COVERED"}
    ]
  }
}
```
