# Post-call deadline states

```mermaid
stateDiagram-v2
    [*] --> Classify
    Classify --> Unstated: all cited turns explicitly have no deadline
    Classify --> Resolved: complete stated expressions support one date
    Classify --> Ambiguous: absent, stale, uncertain or unsupported evidence
    Classify --> Contradictory: supported expressions give different dates
    Unstated --> OptionalReminder: leave due date null
    OptionalReminder --> FilingEligible: other required checks pass
    Resolved --> FilingEligible: other required checks pass
    Ambiguous --> ReviewHold: ask for exact timing
    Contradictory --> ReviewHold: ask which timing applies
```

Every transition is exercised by `test_timing_cases_control_filing_and_retention`,
which runs the committed offline corpus through process_session, real coverage
reconciliation, review_complete and retention_ready. `test_timing_evidence_integrity`
exercises missing, omitted, stale and misattributed source evidence.
No TLC execution is claimed. The model covers deadline validation; identity,
audio, candidate confirmation and required-check holds remain independent gates.

Semantic timing classification is model output. Each source entry explicitly
judges a complete cited speaker turn, rather than only the short task quote.
`unstated` requires every entry to say unstated and contain no expressions.
`uncertain` never becomes an optional reminder. Corrections and alternatives must
remain in the complete expression; unsupported expressions hold review.
The deterministic date grammar matches whole expressions, never an extracted
weekday inside an unsupported expression. Distinct supported dates contradict.
