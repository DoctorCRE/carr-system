# Joe calendar prebrief runtime

`com.carr.calendar-prebrief-joe` is an uninstalled, inactive-by-default
LaunchAgent. It uses only the Joe live profile, the installed CARR Calendar
Access app, and the jobs lease. Dell and every canary definition remain
disabled.

Activation is explicit: create the 0600 provisioner profiles, select Joe
calendars through the app catalog, register the allowlist, and prove Joe
preflight. The installer only stages a recoverable app replacement and plist.
Then use `calendar-prebrief-activation.py seal-activate-joe-live` with the
evidence digest, runtime profile, and installed plist. It requires the typed
authority readback first, atomically changes the runtime profile from `false`
to `true`, and bootstraps, kickstarts, and reads back the exact LaunchAgent.

The manifest remains disabled as the bootstrap default. The sole live exception
is authority-managed: generic control-plane sync preserves it only while the
latest Joe activation receipt matches the current allowlist. A changed
allowlist fences both scheduling and claiming until a new explicit activation.

## Moving to another Mac (done on the Studio 2026-09-27)

Two things do not travel with `~/.config/carr`, and both failed silently here:

1. **The allowlist.** It stores EventKit calendar identifiers, which are local
   to each Mac. The MacBook's allowlist names no calendar on the Studio, so
   every capture would refuse with "configured allowlisted calendar is absent".
   Run the catalog on the new Mac through the installed app (`discover-catalog`,
   then `discover-allowlist` with the chosen index), then
   `calendar-prebrief-activation.py register-allowlist`. Registering changes the
   allowlist and fences the scheduler, so `seal-activate-joe-live` must follow.
2. **The Calendar grant.** macOS ties it to the bundle id plus the bundle's
   ad-hoc cdhash. `bin/build-calendar-access.sh` now keeps a valid build rather
   than recompiling, and the installer copies that build, so the installed app
   and the repo bundle share one cdhash and one grant. A recompile (a new Mac,
   a changed stub, or `--force`) means Joe grants Calendars again: System
   Settings > Privacy & Security > Calendars > CARR Calendar Access.

The scoped database logins do travel: `joe-live-preflight` proves all five
before anything is re-registered, and re-running the provisioner would only
rotate production passwords that already work.

Standing check: `./run.sh health` (jobs section) flags a missed weekday slot or
two receipted runs in a row that read 0 events, once activation is current.

## Unknown attendees are skipped, ambiguous ones refuse (migration 0735)

An outside attendee the record holds no contact for no longer refuses Joe's
whole prebrief: the resolver answers NULL, the coordinator skips that attendee,
and the run reports `unknown_attendees` as a count plus pseudonymous sha256
keys (never an address; anyone holding a candidate address can confirm it
against a key, so treat the keys as pseudonymous, not anonymous) in the child result, the runtime's tick output, and
`out/calendar-prebrief-joe-last-run.json`. `./run.sh health` turns a nonzero
count into intake work (rule d7c69aa6). An attendee matching two or more live
contacts, two or more live party rows (even if only one carries a ref), only
merged ones, only soft-deleted or merged party rows (even if a role row under one
is still live), or a party row with no canonical ref yet still refuses, because a wrong attribution is worse than a
missing one. "Unknown" means no party row carries the address at all, so a
person the record already holds is never reported for intake to create twice.
The count is not in the database receipt. The health finding is time-rolling
but not on the release pipeline's first-appearance allowlist, so the release
gate still diffs it.
