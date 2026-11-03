---- MODULE Competition ----
EXTENDS Naturals, FiniteSets
CONSTANT Forget
VARIABLES evidence, seen, aliases, key, party, body, findings, phase, scans, badMerge, badBinding
vars == <<evidence, seen, aliases, key, party, body, findings, phase, scans, badMerge, badBinding>>
\* One keyless signature has a frozen finding. Keys 77 and 88 are distinct
\* decoder rows with equal Message-ID, timestamp and content proof.
\* lib/outlook_intake.py:1133-1146 freezes this initial observation.
Init == /\ evidence = {} /\ seen = {} /\ aliases = {0}
        /\ key = 0 /\ party = 0 /\ body = 0 /\ findings = {<<0,0,0>>}
        /\ phase = "competing" /\ scans = 0 /\ badMerge = FALSE /\ badBinding = FALSE
\* resolve_sources persists both rows before either can be assigned.
Compete == /\ phase = "competing"
           /\ evidence' = {77,88} /\ seen' = {77,88}
           /\ phase' = "scan"
           /\ UNCHANGED <<aliases, key, party, body, findings, scans, badMerge, badBinding>>
\* Later SQLite batches can omit either row or restore the keyless source.
Scan == /\ phase = "scan" /\ scans < 3 /\ scans' = scans + 1
        /\ key' \in {0,77,88} /\ party' \in {0,1} /\ body' \in {0,1}
        /\ phase' = "resolve"
        /\ UNCHANGED <<evidence, seen, aliases, findings, badMerge, badBinding>>
\* resolve_sources unique-proof branch. Forget models d3bf982e's batch-only
\* proof_competes. Absent competitors are lost before assigning a lone key.
Resolve == /\ phase = "resolve"
           /\ LET retained == IF Forget THEN (IF key = 0 THEN {} ELSE {key}) ELSE evidence
                  exact == key \in aliases
                  unique == ~exact /\ body = 0 /\ Cardinality(retained) <= 1
                  fresh == ~exact /\ ~unique /\ aliases # {0} /\ key # 0
                  valid == fresh \/ ((exact \/ unique) /\ party = 0 /\ body = 0)
              IN /\ evidence' = retained
                 /\ aliases' = IF unique THEN aliases \cup {key} ELSE aliases
                 /\ findings' = IF fresh THEN findings \cup {<<key,party,body>>} ELSE findings
                 /\ badMerge' = (badMerge \/ unique)
                 /\ badBinding' = (badBinding \/ (valid /\ (party # 0 \/ body # 0)))
           /\ phase' = "scan"
           /\ UNCHANGED <<seen, key, party, body, scans>>
\* Run.write receipt loss and journal reload never discard source evidence.
Restart == UNCHANGED vars
Next == Compete \/ Scan \/ Resolve \/ Restart
\* Competing evidence is monotone until resolved. No unique distinguishing
\* evidence is available in this equal-proof history, so none can be discharged.
CompetingEvidenceMonotone == seen \subseteq evidence
UniqueProof == ~badMerge
AtMostOne == Cardinality(findings) <= 1
ChangedPartySourceHold == ~badBinding
====
