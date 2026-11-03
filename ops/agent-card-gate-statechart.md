# Card admission

```mermaid
stateDiagram-v2
    [*] --> PromptParsed
    PromptParsed --> Refused: missing, malformed, misplaced, or multiple Card lines
    PromptParsed --> Lookup: exactly one standalone Card per launch
    Lookup --> Registered: authenticated response with matching Card and valid facts
    Lookup --> Unregistered: registered false
    Lookup --> Timeout: lookup or shared deadline expires
    Lookup --> AuthError: missing or insecure credential, HTTP 401 or 403
    Lookup --> RecordLayerDown: transport, TLS, HTTP, RPC, or response failure
    Registered --> Refused: repository mismatch
    Registered --> Lookup: next distinct Card in batch
    Registered --> Admitted: all Cards verified before shared deadline
    Unregistered --> Refused
    Timeout --> Refused
    AuthError --> Refused
    RecordLayerDown --> Refused
    Admitted --> [*]
    Refused --> [*]
```

Every refusal starts no work. No prior mint or admission can admit offline.
Each lookup has a three-second hard deadline within an eight-second batch
budget and the host's ten-second hook timeout. Repository discovery retains
its half-second bound. A late success refuses.

Tests derive from each edge through the hook JSON stdin entry point. The
existing prompt, Codex, and T3 tests pin parsing and excluded tools. The direct
transport matrix covers registration, response facts, authentication, outage,
and TLS. Deadline tests cover DNS, TLS, headers, trickling bodies, late success,
and shared batch exhaustion. Repository tests cover mismatch, null, worktrees,
and inherited Git environment isolation. Excluded read-only tools do not enter
this chart. The model omits provider execution after admission.
