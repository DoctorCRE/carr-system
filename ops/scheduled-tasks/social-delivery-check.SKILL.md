---
name: social-delivery-check
description: Observe due social posts and retain delivery evidence for the existing watchdog digest.
---

Run the observer from the installed carr-system checkout.

```sh
python3 tools/social_delivery.py
```

The coordinator registers this prompt with the existing Claude Code native scheduled-task scheduler.
Use cron `0 6 * * *` with timezone `America/Chicago`. Registration and enabling happen after merge.
Do not install the unused social-batch-headless replacement or add launchd or another scheduler.
The command uses one cadence configuration value, `INTERVAL_SECONDS` in tools/social_delivery.py,
default 86400. A cadence change must update that value and the native scheduler together.
The CLI also accepts `--interval-seconds` for an explicitly configured scheduler override.

Read every historical batch ledger under out/social-batch. Keep delivery.json there as the private
operational receipt. Do not limit discovery to the latest week or the seven-day listing horizon.
The command only reads publisher status and returned public URLs. Never create, upload, post,
schedule, edit, retry, cancel, move or delete a post. Never call social_batch.submit.
Do not change credentials or send a separate alert. The existing job watchdog ingests receipt findings,
deduplicates unchanged delivery alerts, and reports missing or stale observer coverage.
Exit 1 means coverage, throttling, authentication or deferred reads need attention; it never authorizes
retrying publication. Keep missing IDs, 404s, ambiguous responses and blocked URLs unverified.
Engine seat h owns database measurement. This receipt is an interface handoff only.
