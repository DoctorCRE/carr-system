"""Installation and observation share the same launchd machine scope."""

# RUNS ON EXACTLY ONE MACHINE. Not a statement about Joe; a statement about what
# the job writes. Each of these mutates state that is SHARED between the two
# machines, so a second copy is either duplicated work or a two-writer conflict.
# Widened 2026-08-10 during the Dell migration audit, when the set held only the
# video pipeline and the other five would have been installed on his Mac:
#
#   videopipeline       — Joe's Movies folder; Dell has no video pipeline.
#   nightly-record-layer— pushes the corpus to the shared vault and mirrors
#                         doctrine to a path hardcoded to Joe's Google Drive
#                         (bin/nightly.sh:154), which cannot resolve on another
#                         machine. The cadence engine inside it IS idempotent,
#                         so the risk is the vault writes, not double-spawning.
#   rules-refresh       — writes the shared compiled-rules renders, and the cost
#                         ruling in its own plist is decisive: Neon free is
#                         100 CU-h/month at ~5 min per wake, so a second Mac
#                         waking it hourly doubles the burn and can SUSPEND the
#                         database for the rest of the month.
#   local-briefs        — maintains Joe's local review queue. Legacy brief files
#                         are explicit recovery only; a second scheduler would
#                         duplicate the same owner-specific maintenance.
#   partner-ping        — writes the shared record. One pinger is the point.
#   cutover-watch       — writes the shared record (a loop update on #532) and
#                         holds its own sentinel of what it last reported under
#                         out/cutover-watch/, which is per-machine and would
#                         make two Macs disagree about what is "new" — the
#                         same partner-ping shape (one watcher, one shared
#                         record) with the added risk of two update-loop calls
#                         racing on the same loop's base_version.
#   canary-ingest-sink  — Joe's machine-local canary destination for the
#                         notes-sweep CANARY; a second Mac standing up its own
#                         copy would give the canary tier two different
#                         "isolated" endpoints to disagree about, not defense
#                         in depth.
#
# What the second machine still needs from the nightly is the record-derived
# fetch allowlist, which is per-machine and gitignored. That is why
# com.carr.fetch-allowlist.plist exists as its own job rather than being
# inherited from the nightly chain.
PRIMARY_ONLY = {
    "com.carr.expert-reply-watch.plist",
    "com.carr.job-watchdog.plist",
    "com.carr.videopipeline.plist",
    # com.carr.preflight-watch.plist was listed here until 2026-08-22. It watched
    # DELL's migration packet from Joe's Mac and was built to remove itself once
    # his A15 closed. A15 is closed, the watcher unloaded and deleted its own
    # plist as designed, and bin/preflight-watch.sh is retired with this entry —
    # which had been naming a plist that exists in neither ops/launchd/ nor
    # ~/Library/LaunchAgents. A lifecycle that completes should leave nothing
    # behind pointing at it (rule def3e84e, artifact tombstones: nothing
    # silently rots).
    "com.carr.nightly-record-layer.plist",
    "com.carr.rules-refresh.plist",
    "com.carr.local-briefs.plist",
    "com.carr.partner-ping.plist",
    "com.carr.cutover-watch.plist",
    # Joe's machine-local canary destination for the notes-sweep CANARY; a
    # second Mac must not stand up a second sink.
    "com.carr.canary-ingest-sink.plist",
    # Joe 2026-09-26: the Mac Studio is the hub and the MacBook is a thin client
    # into it, so work that acts on shared state runs on the primary alone.
    # room-bridge: both Macs carried the same Model Room desks and raced for
    # each turn; it also wakes the engineering controller, whose one Worker
    # token lives on the primary.  release-pipeline and control-plane-tick
    # would release and enqueue twice.  The cadence sweep would escalate twice.
    # nightly-exports-daytime-retry is the safety net for nightly-record-layer,
    # which is already primary-only.  timebomb-audit scans the same tracked
    # source on every Mac.  Device-bound jobs (dictation, call mode, capture,
    # keymap, local servers, spool flush, fleet sync) stay on every machine.
    "com.carr.room-bridge.plist",
    "com.carr.release-pipeline.plist",
    "com.carr.control-plane-tick.plist",
    "com.carr.delivery-cadence-a05-sweep.plist",
    "com.carr.nightly-exports-daytime-retry.plist",
    "com.carr.timebomb-audit.plist",
    # WR-000178: the Studio's Tailscale stayed stopped ~6h after the 2026-09-30
    # reboot and cut SSH to the MacBook. The hub is the node that must come up.
    "com.carr.tailscale-up.plist",
}


# The mirror image: jobs only the SECOND machine needs, because the primary
# already gets the same effect from a chain the second machine must not run.
SECONDARY_ONLY = {"com.carr.fetch-allowlist.plist"}


def allowed_on_machine(filename: str, primary: bool) -> bool:
    return not ((filename in PRIMARY_ONLY and not primary)
                or (filename in SECONDARY_ONLY and primary))
