---- MODULE Review ----
EXTENDS Naturals, FiniteSets
VARIABLES phase, findings, writes, frozen, alias, request, firstAlias, otherFindings
vars == <<phase, findings, writes, frozen, alias, request, firstAlias, otherFindings>>
Init == /\ phase = "Unread" /\ findings = {} /\ writes = 0 /\ frozen = FALSE
        /\ alias = "keyless" /\ request = "none" /\ firstAlias = "none" /\ otherFindings = {}
Resolve == /\ phase = "Unread" /\ phase' \in {"Held", "Prepared"}
           /\ frozen' = (phase' = "Prepared")
           /\ request' = IF phase' = "Prepared" THEN alias ELSE request
           /\ firstAlias' = request'
           /\ UNCHANGED <<findings, writes, alias, otherFindings>>
Submit == /\ phase = "Prepared" /\ phase' \in {"Recorded", "Retry", "Refused"}
          /\ findings' \in {findings, findings \cup {request}}
          /\ UNCHANGED <<writes, frozen, alias, request, firstAlias, otherFindings>>
Retry == /\ phase = "Retry" /\ phase' \in {"Recorded", "Changed", "Refused"}
         /\ findings' \in {findings, findings \cup {request}}
         /\ UNCHANGED <<writes, frozen, alias, request, firstAlias, otherFindings>>
AliasChange == /\ alias' = IF alias = "keyless" THEN "keyed" ELSE "keyless"
               /\ UNCHANGED <<phase, findings, writes, frozen, request, firstAlias, otherFindings>>
AmbiguousDuplicate == /\ otherFindings' = {"other-key"}
                      /\ UNCHANGED <<phase, findings, writes, frozen, alias, request, firstAlias>>
Restart == UNCHANGED vars
Replay == /\ phase = "Recorded" /\ findings' = findings \cup {request}
          /\ UNCHANGED <<phase, writes, frozen, alias, request, firstAlias, otherFindings>>
Done == /\ phase \in {"Held", "Changed", "Refused"} /\ UNCHANGED vars
RecoverHeld == /\ phase = "Held" /\ phase' = "Unread"
               /\ UNCHANGED <<findings, writes, frozen, alias, request, firstAlias, otherFindings>>
RestoreSource == /\ phase = "Changed" /\ phase' = "Retry"
                 /\ UNCHANGED <<findings, writes, frozen, alias, request, firstAlias, otherFindings>>
Next == Resolve \/ Submit \/ Retry \/ AliasChange \/ AmbiguousDuplicate \/ Restart
        \/ Replay \/ Done \/ RecoverHeld \/ RestoreSource
NoOverwrite == writes = 0
AtMostOne == Cardinality(findings) <= 1 /\ Cardinality(otherFindings) <= 1
FrozenRetry == phase \in {"Retry", "Recorded", "Refused", "Changed"} => frozen
FrozenAlias == frozen => request = firstAlias
DistinctSources == findings \intersect otherFindings = {}
====
