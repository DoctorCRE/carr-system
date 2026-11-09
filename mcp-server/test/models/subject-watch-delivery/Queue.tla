---- MODULE Queue ----
EXTENDS Naturals, FiniteSets
VARIABLES cursor, locked, emitted, visits, unlocked
vars == <<cursor, locked, emitted, visits, unlocked>>
TypeOK == /\ cursor \in 0..2 /\ locked \subseteq {1,2}
          /\ emitted \subseteq {1,2} /\ visits \subseteq {1,2}
          /\ unlocked \in BOOLEAN
Init == /\ cursor=0 /\ locked={1} /\ emitted={} /\ visits={} /\ unlocked=FALSE
\* migrations/1167_subject_watch_delivery.sql:117. Attempt one candidate, including a locked one.
Attempt == /\ cursor<2
           /\ cursor'=cursor+1 /\ visits'=visits \cup {cursor+1}
           /\ emitted'=IF cursor+1 \in locked THEN emitted ELSE emitted \cup {cursor+1}
           /\ UNCHANGED <<locked,unlocked>>
\* migrations/1167_subject_watch_delivery.sql:208. An exhausted suffix resets the persisted cursor.
Wrap == /\ cursor=2 /\ cursor'=0 /\ visits'={}
        /\ UNCHANGED <<locked,emitted,unlocked>>
\* PostgreSQL peer transaction releases its locks; tested with both row and generation locks.
Unlock == /\ ~unlocked /\ locked'={} /\ unlocked'=TRUE /\ visits'={}
          /\ UNCHANGED <<cursor,emitted>>
Next == Attempt \/ Wrap \/ Unlock
Spec == Init /\ [][Next]_vars
\* An unlocked watch visited in this pass never stays pending behind a locked watch.
FreeWorkProgresses == visits \cap ({1,2} \ locked) \subseteq emitted
\* The locked head never emits before its lock clears.
LockedWorkWaits == ~unlocked => 1 \notin emitted
====
