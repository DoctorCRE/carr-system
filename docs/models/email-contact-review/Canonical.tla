---- MODULE Canonical ----
EXTENDS Naturals, FiniteSets, TLC
CONSTANT Broken
VARIABLES aliases, keys, observation, findings, party, body, alias, real,
          phase, badMerge, badBinding, holder, signatureHolder, receipt, scans
vars == <<aliases, keys, observation, findings, party, body, alias, real,
          phase, badMerge, badBinding, holder, signatureHolder, receipt, scans>>
Sources == {0,1}
Owner(a) == IF a < 2 THEN 0 ELSE 1
\* Two distinct keyless rows have already frozen observations under the same MID.
\* Each observation has its own content proof (source, body=0) and alias set.
Init == /\ aliases = [s \in Sources |-> {2*s}]
        /\ keys = [s \in Sources |-> {}]
        /\ observation = [s \in Sources |-> <<s, 2*s, 0, 0>>]
        /\ findings = {<<0, observation[0]>>, <<1, observation[1]>>}
        /\ party = 0 /\ body = 0 /\ alias = 0 /\ real = 0
        /\ phase = "scan" /\ badMerge = FALSE /\ badBinding = FALSE
        /\ holder = 0 /\ signatureHolder = 0 /\ receipt = FALSE /\ scans = 0
\* lib/email_contact_review.py resolve_sources, exact alias then unique proof.
\* Alias 4 is a new representation with no content proof. It must stay held.
Resolve == /\ phase = "resolve"
           /\ LET exact == {s \in Sources : alias \in aliases[s]}
                  compatible == {s \in Sources : keys[s] = {} \/ alias % 2 = 0 \/ keys[s] = {alias}}
                  proven == IF body = 0 /\ alias # 4 THEN {real} \cap compatible ELSE {}
                  choices == IF exact # {} THEN exact ELSE proven
                  unique == Cardinality(choices) = 1
                  selected == IF Broken = 4 /\ alias = 4 THEN 0
                              ELSE IF unique THEN CHOOSE s \in choices : TRUE ELSE real
                  contaminate == Broken = 3 /\ alias = 2 /\ signatureHolder = 1
                  replace == (Broken = 1 /\ alias % 2 = 1 /\ alias \notin aliases[real]) \/
                             (Broken = 2 /\ alias = holder /\ signatureHolder # holder) \/
                             (Broken = 3 /\ alias = 3 /\ keys[1] = {1})
                  frozen == IF replace THEN <<real, alias, party, body>> ELSE observation[selected]
                  valid == (unique \/ replace) /\ frozen[3] = party /\ frozen[4] = body
              IN /\ aliases' = IF unique THEN [aliases EXCEPT ![selected] = @ \cup {alias}] ELSE aliases
                 /\ keys' = IF contaminate THEN [keys EXCEPT ![1] = {1}]
                            ELSE IF unique /\ alias % 2 = 1
                            THEN [keys EXCEPT ![selected] = {alias}] ELSE keys
                 /\ observation' = IF replace THEN [observation EXCEPT ![real] = frozen] ELSE observation
                 /\ findings' = IF valid THEN findings \cup {<<real, frozen>>} ELSE findings
                 /\ badMerge' = (badMerge \/ (unique /\ selected # real) \/ (Broken = 4 /\ alias = 4))
                 /\ badBinding' = (badBinding \/ (valid /\ (party # 0 \/ body # 0)))
           /\ signatureHolder' = alias /\ phase' = "scan"
           /\ UNCHANGED <<party, body, alias, real, holder, receipt, scans>>
\* Run.write is idempotent after a lost response. Receipt loss changes no token.
Retry == /\ phase = "scan" /\ receipt' \in BOOLEAN
         /\ UNCHANGED <<aliases, keys, observation, findings, party, body, alias, real,
                        phase, badMerge, badBinding, holder, signatureHolder, scans>>
\* SQLite scan + contact refresh; activity may retain its earlier holder.
Scan == /\ phase = "scan" /\ scans < 6 /\ scans' = scans + 1
        /\ alias' \in {0,1,2,3,4} /\ real' = IF alias' = 4 THEN 0 ELSE Owner(alias')
        /\ party' \in {0,1} /\ body' \in {0,1}
        /\ holder' \in {holder, alias} /\ phase' = "resolve"
        /\ UNCHANGED <<aliases, keys, observation, findings, badMerge, badBinding, signatureHolder, receipt>>
Restart == UNCHANGED vars
Next == Scan \/ Resolve \/ Retry \/ Restart
\* No real signature may produce a second finding. Run.signature_findings / Run.write.
AtMostOne == \A s \in Sources : Cardinality({f \in findings : f[1] = s}) <= 1
\* Changed party or source content must hold the frozen request. Run.signature_findings.
ChangedPartySourceHold == ~badBinding
\* A representation may join only one proven source. resolve_sources.
UniqueProof == ~badMerge
====
