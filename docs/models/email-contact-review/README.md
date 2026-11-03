# Email signature review

The intake retains contact snapshots and resolves signatures by exact sender address.
Multiple roles may name one party. Shared addresses, absent snapshots and conflicting
snapshots stay held. The signature stage runs independently of activity filing so a
previously filed message can still supply a finding when its full text arrives.

The data shape is one immutable observation per mailbox/source, with a source/party
binding and a list of frozen record-finding requests. The existing intake journal
owns transport attempts, backoff, refusal and acknowledgement. A pure planner owns
field comparison and provenance. This was chosen over a second retry queue because
the journal already persists payloads before sending and preserves lost-response
idempotency. Snapshot versions are evidence, never authority to update a party.

The statechart preceded implementation. TLC explored 11 states and 27 steps.
NoOverwrite, AtMostOne, FrozenRetry and NoDeadlock held. Seven stuttering steps
are omitted from the chart. The finite model abstracts transport and extraction;
synthetic Python tests exercise their actual implementation. It assumes the
record-layer idempotency envelope honors a repeated key and frozen payload.
No production submission was performed.

Transitions map to `ops/email-contact-review-selftest.py`.
Resolve/Held maps to `test_ambiguous_unmatched_suppressed_and_deal_only_are_held`.
Resolve/Prepared/Recorded maps to
`test_signature_change_creates_one_finding_and_never_overwrites_party`.
Submit/Retry/Recorded and Replay map to `test_replay_and_lost_response_use_frozen_arguments`.
Held/Unread maps to `test_held_source_can_resolve_when_full_text_arrives`.
Changed/Retry maps to the intake integration test below.
Retry/Changed maps to `test_changed_source_or_party_never_replays_or_rebinds`.
Submit/Refused and Retry/Refused map to `test_refusal_and_transient_failure_are_bounded`.
`IntakeTest.test_signature_stage_runs_for_filed_mail_and_keeps_retry_guard`
checks the real intake path cannot replay a request before source validation.

Reproduce with the installed pstack statechart runner and these files.
Set TLA2TOOLS to tla2tools.jar and JAVA to Java 11 or later when mise is absent.

Throughput checkpoint. Bootstrap and contract reads came first. Planning and intake
integration share the same state and run serially. There is one owner and no fan-out.
The smallest safe decomposition is a pure planner and the existing intake journal.

The sender snapshot has no atomic server-side identity precondition for findings.
The intake checks the current snapshot before each attempt; this does not provide
an atomic guard against a concurrent server-side merge during transport. Findings
carry the observed party ID and versions and never mutate identity or contact facts.
