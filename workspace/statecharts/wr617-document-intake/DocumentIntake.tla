------------------------ MODULE DocumentIntake ------------------------
EXTENDS Naturals
CONSTANT Mutation
VARIABLES phase, verified, attachments, visible, session, unauthorizedAccess,
          terminalSeen, listed
vars == <<phase, verified, attachments, visible, session, unauthorizedAccess,
          terminalSeen, listed>>

Init == /\ phase = "Ready" /\ verified = FALSE /\ attachments = 0
        /\ visible \in BOOLEAN /\ session \in BOOLEAN
        /\ unauthorizedAccess = FALSE /\ terminalSeen = FALSE /\ listed = FALSE

Authorized == visible /\ session
\* deal-deliverables.js:authorizeDeliverable and deal-deliverables-web.js session checks.
Access == Authorized \/ Mutation = "InvisibleAccess"
Reserve == /\ phase = "Ready" /\ Authorized
           /\ phase' = "Reserved"
           /\ UNCHANGED <<verified, attachments, visible, session,
                          unauthorizedAccess, terminalSeen, listed>>
Upload == /\ phase = "Reserved" /\ Access
          /\ phase' = "Uploading"
          /\ unauthorizedAccess' = (unauthorizedAccess \/ ~Authorized)
          /\ UNCHANGED <<verified, attachments, visible, session, terminalSeen, listed>>
\* deal-deliverables-web.js:verifyDeliverableBytes and verified update in one transaction.
Verify == /\ phase = "Uploading" /\ phase' = "Verified" /\ verified' = TRUE
          /\ UNCHANGED <<attachments, visible, session, unauthorizedAccess, terminalSeen, listed>>
UploadFailure == /\ phase = "Uploading" /\ phase' = "Reserved"
                 /\ UNCHANGED <<verified, attachments, visible, session,
                                unauthorizedAccess, terminalSeen, listed>>
\* deal-deliverables.js:finalize-deal-deliverable rejects expired and unverified rows.
Finalize == /\ (phase = "Verified"
                \/ (Mutation = "UnverifiedFile" /\ phase = "Reserved")
                \/ (Mutation = "TerminalFinalize" /\ phase \in {"Abandoned", "Expired"} /\ verified))
            /\ Authorized /\ phase' = "Filed" /\ attachments' = 1
            /\ UNCHANGED <<verified, visible, session, unauthorizedAccess, terminalSeen, listed>>
FinalizeFailure == /\ phase = "Verified" /\ UNCHANGED vars
\* The row lock and finalized replay branch prevent a second insertion.
ReplayFinalize == /\ phase = "Filed" /\ Authorized
                  /\ attachments' = IF Mutation = "DuplicateAttachment" THEN 2 ELSE attachments
                  /\ UNCHANGED <<phase, verified, visible, session, unauthorizedAccess, terminalSeen, listed>>
ReadReceipt == /\ phase # "Ready" /\ Access
               /\ unauthorizedAccess' = (unauthorizedAccess \/ ~Authorized)
               /\ UNCHANGED <<phase, verified, attachments, visible, session, terminalSeen, listed>>
\* Abandoned is stored as expired; the cause differs but both are terminal.
Abandon == /\ phase \in {"Reserved", "Verified"} /\ Authorized
           /\ phase' = "Abandoned" /\ terminalSeen' = TRUE
           /\ UNCHANGED <<verified, attachments, visible, session, unauthorizedAccess, listed>>
Expire == /\ phase \in {"Reserved", "Verified"}
          /\ phase' = "Expired" /\ terminalSeen' = TRUE
          /\ UNCHANGED <<verified, attachments, visible, session, unauthorizedAccess, listed>>
ReplayReserve == /\ phase = "Reserved" /\ Authorized /\ UNCHANGED vars
ReplayAbandon == /\ phase = "Abandoned" /\ Authorized /\ UNCHANGED vars
RefuseExpired == /\ phase = "Expired" /\ UNCHANGED vars
RefuseFiledAbandon == /\ phase = "Filed" /\ Authorized /\ UNCHANGED vars
\* deal-deliverables.js:list-deal-deliverables excludes document-intakes object keys.
ListDeliverables == /\ Authorized /\ listed' = (Mutation = "PublishedDocument" /\ phase = "Filed")
                    /\ UNCHANGED <<phase, verified, attachments, visible, session, unauthorizedAccess, terminalSeen>>

Revoke == /\ phase # "Ready" /\ (visible \/ session)
          /\ visible' = FALSE /\ session' = FALSE
          /\ UNCHANGED <<phase, verified, attachments, unauthorizedAccess, terminalSeen, listed>>

Next == Revoke \/ Reserve \/ Upload \/ Verify \/ UploadFailure \/ Finalize \/ FinalizeFailure
        \/ ReplayFinalize \/ ReadReceipt \/ Abandon \/ Expire \/ ReplayReserve
        \/ ReplayAbandon \/ RefuseExpired \/ RefuseFiledAbandon \/ ListDeliverables
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in {"Ready", "Reserved", "Uploading", "Verified", "Filed", "Abandoned", "Expired"}
          /\ attachments \in 0..2
          /\ <<verified, visible, session, unauthorizedAccess, terminalSeen, listed>> \in BOOLEAN \X BOOLEAN \X BOOLEAN \X BOOLEAN \X BOOLEAN \X BOOLEAN
\* never Filed without verified bytes
VerifiedBeforeFiled == phase = "Filed" => verified
\* never More than one attachment for a reservation
OneAttachment == attachments <= 1
\* never Receipt or upload for a non-visible deal (or inactive session)
VisibleAccessOnly == ~unauthorizedAccess
\* never Finalize after abandon or expiry
TerminalCannotFinalize == terminalSeen => phase # "Filed"
\* never Document listed as a publishable deliverable
DocumentNeverPublishable == ~listed
=============================================================================
