---- MODULE Canonical ----
EXTENDS Naturals, FiniteSets, TLC
CONSTANT Broken
VARIABLES aliases, keys, competitors, observation, requests, findings, party,
          sourceBody, alias, real, response, activity, holder, phase, badMerge, badBinding
vars == <<aliases, keys, competitors, observation, requests, findings, party,
          sourceBody, alias, real, response, activity, holder, phase, badMerge, badBinding>>
Sources == {0,1}
Aliases == {0,1,2,3}
Owner(a) == IF a < 2 THEN 0 ELSE 1
Init == /\ aliases = [s \in Sources |-> {}]
        /\ keys = [s \in Sources |-> {}]
        /\ competitors = {} /\ observation = [s \in Sources |-> <<>>]
        /\ requests = {} /\ findings = {} /\ party = 0 /\ sourceBody = 0
        /\ alias = 0 /\ real = 0 /\ response = "ok" /\ activity = "pending"
        /\ holder = 0 /\ phase = "scan" /\ badMerge = FALSE /\ badBinding = FALSE
\* email_contact_review.resolve_sources: source records never move; exact aliases
\* or unique content proof select one source, otherwise the representation holds.
Resolve == /\ phase = "resolve"
           /\ LET selected == IF Broken = 3 /\ competitors = Sources /\ real = 1 /\ alias = 2
                              THEN 0 ELSE real
                  token == IF observation[selected] = <<>>
                           THEN <<selected, alias, party, sourceBody>> ELSE observation[selected]
                  replace == (Broken = 1 /\ alias \notin aliases[real]) \/
                             (Broken = 2 /\ alias = holder /\ alias \notin aliases[real]) \/
                             (Broken = 3 /\ alias = 3 /\ keys[real] = {1})
                  frozen == IF replace THEN <<real, alias, party, sourceBody>> ELSE token
              IN /\ aliases' = [aliases EXCEPT ![selected] = @ \cup {alias}]
                 /\ keys' = [keys EXCEPT ![real] = IF selected # real THEN {1} ELSE @]
                 /\ observation' = [observation EXCEPT ![real] = frozen]
                 /\ requests' = IF frozen[3] = party /\ frozen[4] = sourceBody
                                 THEN requests \cup {<<real, frozen>>} ELSE requests
                 /\ badMerge' = badMerge \/ selected # real
                 /\ badBinding' = badBinding \/ (observation[real] # <<>> /\
                      (observation[real][3] # party \/ observation[real][4] # sourceBody) /\
                      frozen[3] = party /\ frozen[4] = sourceBody)
           /\ competitors' = competitors \cup {real} /\ phase' = "submit"
           /\ UNCHANGED <<findings, party, sourceBody, alias, real, response, activity, holder>>
\* Run.write persists intent; lost replies leave the same request for retry.
Submit == /\ phase = "submit" /\ findings' = findings \cup requests
          /\ phase' = "scan" /\ holder' = IF activity = "pending" THEN holder ELSE alias
          /\ UNCHANGED <<aliases, keys, competitors, observation, requests, party,
                         sourceBody, alias, real, response, activity, badMerge, badBinding>>
Scan == /\ phase = "scan" /\ alias' \in Aliases /\ real' = Owner(alias')
        /\ party' \in {0,1} /\ sourceBody' \in {0,1}
        /\ response' \in {"ok", "lost"} /\ activity' \in {"pending", "done"}
        /\ phase' = "resolve"
        /\ UNCHANGED <<aliases, keys, competitors, observation, requests, findings,
                       holder, badMerge, badBinding>>
Restart == UNCHANGED vars
Next == Scan \/ Resolve \/ Submit \/ Restart
AtMostOne == \A s \in Sources : Cardinality({f \in findings : f[1] = s}) <= 1
ChangedPartySourceHold == ~badBinding
UniqueProof == ~badMerge
====
