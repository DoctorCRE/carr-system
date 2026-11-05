------------------------ MODULE Deliverables ------------------------
EXTENDS Naturals
VARIABLES phase, shared, version, authorized, downloaded
vars == <<phase, shared, version, authorized, downloaded>>
Init == /\ phase = "absent" /\ shared = FALSE /\ version = 0 /\ authorized = TRUE /\ downloaded = FALSE
Start == /\ authorized /\ phase = "absent" /\ phase' = "started" /\ UNCHANGED <<shared, version, authorized, downloaded>>
Claim == /\ authorized /\ phase = "started" /\ phase' = "uploading" /\ UNCHANGED <<shared, version, authorized, downloaded>>
Abort == /\ phase = "uploading" /\ phase' = "started" /\ UNCHANGED <<shared, version, authorized, downloaded>>
Expire == /\ phase = "started" /\ phase' = "expired" /\ UNCHANGED <<shared, version, authorized, downloaded>>
Verify == /\ authorized /\ phase = "uploading" /\ phase' = "verified" /\ UNCHANGED <<shared, version, authorized, downloaded>>
Finalize == /\ authorized /\ phase = "verified" /\ phase' = "finalized" /\ version' = 1 /\ UNCHANGED <<shared, authorized, downloaded>>
Share == /\ authorized /\ phase = "finalized" /\ version < 3 /\ shared' = ~shared /\ version' = version + 1 /\ UNCHANGED <<phase, authorized, downloaded>>
Download == /\ authorized /\ phase = "finalized" /\ downloaded' = TRUE /\ UNCHANGED <<phase, shared, version, authorized>>
Revoke == /\ authorized /\ authorized' = FALSE /\ downloaded' = FALSE /\ UNCHANGED <<phase, shared, version>>
Refuse == UNCHANGED vars
Next == Start \/ Claim \/ Abort \/ Expire \/ Verify \/ Finalize \/ Share \/ Download \/ Revoke \/ Refuse
PrivateBeforeFinalization == phase # "finalized" => ~shared
DownloadAuthorized == downloaded => authorized /\ phase = "finalized"
VersionedVisibility == shared => version >= 2
Spec == Init /\ [][Next]_vars
=====================================================================
