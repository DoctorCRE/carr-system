# Watch alert consumption

The notification feed validates the producer's relative destination. Home groups existing notifications by subject and resolves a lease through its client to a readable name and expiration. Both remain actor-scoped bounded reads. No subscription or notification producer changes.

The model isolates notification visibility, Home placement and canonical subject state. Acknowledge changes visibility; settle, un-settle and snooze change placement. None completes the subject. The `watch alerts reach named Home cards and read, settle, snooze stay independent` PostgreSQL test executes every modeled transition for deal and lease alerts via registered handlers, with real producer responses. Each nested test names the starting visibility, placement and operation. Fixture resets establish each starting state independently.

The model excludes domain completion, concurrent source edits, snooze expiry, delivery, tenant isolation and CAS conflicts. Existing Home and watch suites cover those mechanisms. This change adds no transitions to those mechanisms.

Regenerate the checked chart from the model with the installed statechart skill's `from-spec WatchInbox.tla --map WatchInbox.map.ts`. TLC checks TypeOK, ReadCompletesWork, SettlementCompletesWork and SnoozeCompletesWork. The initial hand chart's unreachable Completed state is retained in the model's domain to state the three never rules; the checked chart contains only reachable states.
