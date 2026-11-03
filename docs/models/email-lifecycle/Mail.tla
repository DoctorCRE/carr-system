---- MODULE Mail ----
EXTENDS Naturals, TLC
VARIABLES state, sourceState, version, actions, noted, sent, terminalSeen
vars == <<state, sourceState, version, actions, noted, sent, terminalSeen>>
Terminal == {"deleted","recalled","superseded"}
Init == state = "live" /\ sourceState = "live" /\ version = 0
        /\ actions = 0 /\ noted = 0 /\ sent = FALSE /\ terminalSeen = FALSE
\* mcp-server/src/tools.js:4597 log-activity: identity capture/retry checks the selected terminal capture.
Capture == state = "live" /\ UNCHANGED vars
\* mcp-server/src/email-identity.js:25 guardEmailAction protects source actions.
Act == state = "live" /\ sourceState = "live" /\ actions < 2
       /\ actions' = actions + 1
       /\ UNCHANGED <<state,sourceState,version,noted,sent,terminalSeen>>
\* mcp-server/src/tools.js:4434 record-email-lifecycle: identity lock, terminal write and prior-action notes.
Observe == state = "live" /\ state' \in Terminal /\ version' = 1
           /\ noted' = actions /\ terminalSeen' = TRUE
           /\ UNCHANGED <<sourceState,actions,sent>>
\* mcp-server/src/tools.js:4434 observes the independent inbound identity as well.
ObserveSource == sourceState = "live" /\ sourceState' \in Terminal
                 /\ UNCHANGED <<state,version,actions,noted,sent,terminalSeen>>
\* mcp-server/src/email-proposals.js:535 record-email-sent: guard source, then destination under identity lock.
ConfirmSent == sourceState = "live" /\ state = "live" /\ ~sent
               /\ sent' = TRUE /\ actions' = actions + 1
               /\ UNCHANGED <<state,sourceState,version,noted,terminalSeen>>
\* mcp-server/src/email-proposals.js:500 completed receipts replay without rewriting activity.
ReplaySent == sourceState = "live" /\ sent /\ UNCHANGED vars
Replay == state # "live" /\ UNCHANGED vars
RefuseStaleOrForeign == UNCHANGED vars
Next == Capture \/ Act \/ Observe \/ ObserveSource \/ ConfirmSent \/ ReplaySent
        \/ Replay \/ RefuseStaleOrForeign
Spec == Init /\ [][Next]_vars
\* Earlier actions stay noted when the captured email is terminal.
TerminalNotes == state # "live" => actions = noted
\* A terminal observation retains its lifecycle version.
Versioned == (state = "live" /\ version = 0) \/ (state # "live" /\ version = 1)
\* A deleted, recalled or superseded item never becomes live through a late receipt.
NoResurrection == terminalSeen => state \in Terminal
====
