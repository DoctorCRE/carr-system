---- MODULE Sources ----
EXTENDS Naturals, FiniteSets, TLC
CONSTANT BrokenAlias
VARIABLES alias, holder, signatureHolder, observationIndex, mode, version,
          party, body, first, findings, changedAttempt, activityPending
vars == <<alias, holder, signatureHolder, observationIndex, mode, version,
          party, body, first, findings, changedAttempt, activityPending>>
Init == /\ alias = 0 /\ holder = 0 /\ signatureHolder = 0
        /\ observationIndex = [a \in {0,1} |-> "none"]
        /\ mode = "Review" /\ version = 0 /\ party = 0 /\ body = 0
        /\ first = "none" /\ findings = {} /\ changedAttempt = FALSE
        /\ activityPending \in BOOLEAN
\* outlook_intake.py:1060-1095 reconcile. The old implementation consults only
\* the activity holder; the fixed implementation also consults signature_ids.
Reconcile == /\ mode = "Reconcile"
             /\ LET prior == IF BrokenAlias THEN holder ELSE signatureHolder
                    move == prior # alias /\ observationIndex[prior] # "none"
                IN observationIndex' = IF move
                   THEN [observationIndex EXCEPT ![alias] = observationIndex[prior], ![prior] = "none"]
                   ELSE observationIndex
             /\ signatureHolder' = alias /\ mode' = "Review"
             /\ UNCHANGED <<alias, holder, version, party, body, first, findings, changedAttempt, activityPending>>
\* outlook_intake.py:1110-1155 signature_findings persists before transport.
\* Token is the frozen request plus its original party/body binding. Version
\* changes make newly prepared UUIDs distinct, but never invalidate old bindings.
Review == /\ mode = "Review"
          /\ LET token == IF observationIndex[alias] = "none"
                          THEN <<alias, version, party, body>> ELSE observationIndex[alias]
                 valid == token[3] = party /\ token[4] = body
             IN /\ observationIndex' = [observationIndex EXCEPT ![alias] = token]
                /\ findings' = IF valid THEN findings \cup {token} ELSE findings
                /\ first' = IF first = "none" THEN token ELSE first
                /\ changedAttempt' = changedAttempt \/
                     (valid /\ first # "none" /\ (first[3] # party \/ first[4] # body))
          /\ mode' = "Activity"
          /\ UNCHANGED <<alias, holder, signatureHolder, version, party, body, activityPending>>
\* outlook_intake.py:1081-1095 retains unresolved activity identity.
Activity == /\ mode = "Activity" /\ holder' = IF activityPending THEN holder ELSE alias
            /\ mode' = "Idle"
            /\ UNCHANGED <<alias, signatureHolder, observationIndex, version, party, body, first, findings, changedAttempt, activityPending>>
\* decoder input to Run.emails; includes repeat scans and both alias directions.
Scan == /\ mode = "Idle" /\ alias' \in {0,1} /\ mode' = "Reconcile"
        /\ UNCHANGED <<holder, signatureHolder, observationIndex, version, party, body, first, findings, changedAttempt, activityPending>>
\* Contact snapshot refresh / changed full source between intake invocations.
ChangeSnapshot == /\ mode = "Idle" /\ version' \in {0,1} /\ party' \in {0,1} /\ body' \in {0,1}
                  /\ UNCHANGED <<alias, holder, signatureHolder, observationIndex, mode, first, findings, changedAttempt, activityPending>>
\* Persisted JSON and journal load preserve all identity associations.
Restart == UNCHANGED vars
Next == Reconcile \/ Review \/ Activity \/ Scan \/ ChangeSnapshot \/ Restart
AtMostOne == Cardinality(findings) <= 1
ChangedPartySourceHold == ~changedAttempt
====
