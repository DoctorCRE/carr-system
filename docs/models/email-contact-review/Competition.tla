---- MODULE Competition ----
EXTENDS Naturals, FiniteSets
CONSTANT Forget
VARIABLES evidence, seen, aliases, key, party, body, findings, phase, scans, badMerge, badBinding, excluded
vars == <<evidence, seen, aliases, key, party, body, findings, phase, scans, badMerge, badBinding, excluded>>
\* One keyless signature has a frozen finding. Keys 77 and 88 are distinct
\* decoder rows with equal Message-ID, timestamp and content proof.
\* lib/outlook_intake.py:1133-1146 freezes this initial observation.
Init == /\ excluded = {} /\ evidence = {} /\ seen = {} /\ aliases = {0}
        /\ key = 0 /\ party = 0 /\ body = 0 /\ findings = {<<0,0,0>>}
        /\ phase = "competing" /\ scans = 0 /\ badMerge = FALSE /\ badBinding = FALSE
\* lib/email_contact_review.py:71-77 persists both rows before either is assigned.
Compete == /\ phase = "competing"
           /\ evidence' = {77,88} /\ seen' = {77,88}
           /\ phase' = "scan"
           /\ UNCHANGED <<aliases, key, party, body, findings, scans, badMerge, badBinding, excluded>>
\* lib/outlook_intake.py:820-829 supplies raw rows; signature_findings:1114
\* resolves each returning batch. Rows may disappear or the keyless row return.
Scan == /\ phase = "scan" /\ scans < 3 /\ scans' = scans + 1
        /\ key' \in {0,77,88} /\ party' \in {0,1} /\ body' \in {0,1}
        /\ phase' = "resolve"
        /\ UNCHANGED <<evidence, seen, aliases, findings, badMerge, badBinding, excluded>>
\* lib/email_contact_review.py:101-121 unique-proof branch. Forget models d3bf982e's batch-only
\* proof_competes. Absent competitors are lost before assigning a lone key.
Resolve == /\ phase = "resolve"
           /\ LET retained == IF Forget THEN (IF key = 0 THEN {} ELSE {key}) ELSE evidence
                  exact == key \in aliases
                  eligible == retained \ excluded
                  unique == ~exact /\ key \notin excluded /\ body = 0 /\ Cardinality(eligible) <= 1
                  fresh == ~exact /\ ~unique /\ aliases # {0} /\ key # 0 /\ key \notin excluded
                  valid == fresh \/ ((exact \/ unique) /\ party = 0 /\ body = 0)
              IN /\ evidence' = retained
                 /\ aliases' = IF unique THEN aliases \cup {key} ELSE aliases
                 /\ findings' = IF fresh THEN findings \cup {<<key,party,body>>} ELSE findings
                 /\ badMerge' = (badMerge \/ (unique /\ excluded = {}))
                 /\ badBinding' = (badBinding \/ (valid /\ (party # 0 \/ body # 0)))
           /\ phase' = "scan"
           /\ UNCHANGED <<seen, key, party, body, scans, excluded>>
\* A later key-88 row uniquely matches a second, distinct frozen source.
\* lib/email_contact_review.py:94-104 excludes aliases assigned to another source.
Prove == /\ phase = "scan" /\ excluded = {} /\ aliases = {0}
         /\ excluded' = {88}
         /\ UNCHANGED <<evidence, seen, aliases, key, party, body, findings, phase, scans, badMerge, badBinding>>
\* lib/outlook_intake.py:975-1015 and :1118 preserve evidence over receipt loss
\* and journal reload. Restart stutters only persisted signature state.
Restart == UNCHANGED vars
Next == Compete \/ Scan \/ Resolve \/ Prove \/ Restart
\* Competing evidence is monotone until resolved. Proof may exclude a candidate, but
\* the underlying observations remain durable even after that resolution.
CompetingEvidenceMonotone == seen \subseteq evidence
UniqueProof == ~badMerge
AtMostOne == Cardinality(findings) <= 1
ChangedPartySourceHold == ~badBinding
====
