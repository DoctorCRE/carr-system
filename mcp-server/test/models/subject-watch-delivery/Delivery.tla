---- MODULE Delivery ----
EXTENDS Naturals
VARIABLES watch, receipt, inbox, health, emitted, removedAt, mailbox, allowed, unwatchConfirmed, readStatus
vars == <<watch, receipt, inbox, health, emitted, removedAt, mailbox, allowed, unwatchConfirmed, readStatus>>
TypeOK == /\ watch \in {"Watching","Paused","Removed"}
          /\ receipt \in {"Quiet","Pending","Emitted"}
          /\ inbox \in {"Quiet","Visible","Settled"}
          /\ health \in {"Healthy","Delayed","NeedsRepair"}
          /\ emitted \in 0..1 /\ removedAt \in 0..1
          /\ mailbox \in {"Own","Other"} /\ allowed \in BOOLEAN
          /\ unwatchConfirmed \in BOOLEAN /\ readStatus \in {"Watching","NotWatching","Unavailable"}
Init == /\ watch="Watching" /\ receipt="Quiet" /\ inbox="Quiet" /\ health="Healthy"
        /\ unwatchConfirmed=FALSE /\ readStatus="Watching"
        /\ emitted=0 /\ removedAt=1 /\ mailbox \in {"Own","Other"} /\ allowed=TRUE
\* migrations/1166_subject_watch_changes.sql:117
Capture == /\ watch="Watching" /\ receipt="Quiet" /\ mailbox="Own"
           /\ receipt'="Pending" /\ UNCHANGED <<watch,inbox,health,emitted,removedAt,mailbox,allowed,unwatchConfirmed,readStatus>>
\* migrations/1167_subject_watch_delivery.sql:107
Emit == /\ watch="Watching" /\ receipt="Pending" /\ health#"NeedsRepair" /\ allowed /\ mailbox="Own"
        /\ receipt'="Emitted" /\ inbox'="Visible" /\ emitted'=emitted+1 /\ health'="Healthy"
        /\ UNCHANGED <<watch,removedAt,mailbox,allowed,unwatchConfirmed,readStatus>>
\* migrations/1167_subject_watch_delivery.sql:57
Pause == /\ watch="Watching" /\ watch'="Paused"
         /\ receipt'=IF receipt="Pending" THEN "Quiet" ELSE receipt
         /\ UNCHANGED <<inbox,health,emitted,removedAt,mailbox,allowed,unwatchConfirmed,readStatus>>
\* migrations/1167_subject_watch_delivery.sql:67
Resume == /\ watch="Paused" /\ watch'="Watching"
          /\ UNCHANGED <<receipt,inbox,health,emitted,removedAt,mailbox,allowed,unwatchConfirmed,readStatus>>
\* migrations/1167_subject_watch_delivery.sql:274
Unwatch == /\ watch#"Removed" /\ watch'="Removed" /\ removedAt'=emitted /\ unwatchConfirmed'=TRUE
           /\ receipt'=IF receipt="Pending" THEN "Quiet" ELSE receipt
           /\ UNCHANGED <<inbox,health,emitted,mailbox,allowed,readStatus>>
\* migrations/1167_subject_watch_delivery.sql:166
Failure == /\ receipt="Pending" /\ health#"NeedsRepair"
           /\ health'=IF health="Healthy" THEN "Delayed" ELSE "NeedsRepair"
           /\ UNCHANGED <<watch,receipt,inbox,emitted,removedAt,mailbox,allowed,unwatchConfirmed,readStatus>>
\* migrations/1167_subject_watch_delivery.sql:68
Repair == /\ health="NeedsRepair" /\ health'="Healthy"
          /\ UNCHANGED <<watch,receipt,inbox,emitted,removedAt,mailbox,allowed,unwatchConfirmed,readStatus>>
\* migrations/1167_subject_watch_delivery.sql:118
AccessLost == /\ allowed /\ allowed'=FALSE
              /\ watch'=IF watch="Watching" THEN "Paused" ELSE watch
              /\ receipt'=IF receipt="Pending" THEN "Quiet" ELSE receipt
              /\ UNCHANGED <<inbox,health,emitted,removedAt,mailbox,unwatchConfirmed,readStatus>>
\* migrations/1140_home_inbox_resolution_adapters.sql:1
Settle == /\ inbox="Visible" /\ inbox'="Settled"
          /\ UNCHANGED <<watch,receipt,health,emitted,removedAt,mailbox,allowed,unwatchConfirmed,readStatus>>
Replay == UNCHANGED vars
\* mcp-server/src/subject-watches.js:64
ReadFailure == /\ readStatus'="Unavailable"
               /\ UNCHANGED <<watch,receipt,inbox,health,emitted,removedAt,mailbox,allowed,unwatchConfirmed>>
\* migrations/1167_subject_watch_delivery.sql:278
ReadSuccess == /\ readStatus'=IF watch="Removed" THEN "NotWatching" ELSE "Watching"
               /\ UNCHANGED <<watch,receipt,inbox,health,emitted,removedAt,mailbox,allowed,unwatchConfirmed>>
Next == ReadFailure \/ ReadSuccess \/ Capture \/ Emit \/ Pause \/ Resume \/ Unwatch \/ Failure \/ Repair \/ AccessLost \/ Settle \/ Replay
Spec == Init /\ [][Next]_vars
\* One semantic change never produces duplicate notifications.
NoDuplicates == emitted<=1
\* No pending change emits after confirmed removal.
NoPostRemoval == watch="Removed" => emitted=removedAt
\* Starting or resuming never retains historical delivery backlog.
QuietResume == ~(watch="Paused" /\ receipt="Pending")
\* Another mailbox's private reply never becomes visible.
MailboxIsolation == mailbox="Other" => emitted=0
\* Settling or acknowledging an alert never removes its watch.
SettleKeepsWatch == watch="Removed" => unwatchConfirmed
\* A failed read is never displayed as confirmed unwatched.
ReadKeepsWatch == readStatus="NotWatching" => watch="Removed"
====
