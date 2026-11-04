Card: WR617.S3.J2

## System design
One unit, one owner. Block first on chart, identity and receipt boundaries. No fan-out. Serialize per tenant/actor for the quota and per action through the existing envelope. No child agents per brief.

Compared application-managed SQL upserts with one definer lifecycle door. Chose the definer so runtime roles cannot bypass ownership, quota or history. Three MCP verbs share existing authentication and preference authority. No HTTP door, delivery transport, queue or preference mutation.

Subscription versions retain immutable endpoint/key snapshots, with old versions revoked on replacement. Expiry is a time-derived state and cannot remain eligible after its deadline. Exact active refresh retains generation; replacement or reactivation increments generation. Receipts return historical action results, never proof of current delivery eligibility.

Capacity is three active installations per tenant/actor (6 today, 60 at 10x). Indexed owner/installation lookups and serialized bounded quota reads avoid N+1 work. First bottleneck at larger scale is per-actor contention, isolated to that actor. No cache. Public configuration is read per call; generation changes take immediate effect. No stale ownership or write fallback. DB transaction commit precedes RPC response. No async lifecycle work.

External endpoint network calls are forbidden. Existing transaction/request timeouts bound the single DB call; SQL lock_timeout 2 seconds and statement_timeout 5 seconds, no retry loop. Failure returns a generic sanitized refusal. No endpoint/key material enters envelope args or events; only a SHA-256 request digest does. DB exceptions are discarded before shared public error diagnostics.

At most 10 successful lifecycle actions per actor per rolling minute, including revoke, plus existing global request budget; exact action retries consume no quota. Rejected actions make no writes. Actor lock closes concurrent quota races. Every write has a UUID idempotency key, verb and exact-request digest. No pushes, paid infrastructure, private VAPID reads or changes to opt-in.

Verification maps to lifecycle/ownership, concurrent cap, exact/conflicting replay, malformed input, public generation changes, missing config, no endpoint fetch, sanitized errors, grants, unchanged preferences and rolling action budget. Statechart is behavioral guidance, not a checked formal proof (TLC unavailable).
