"""Installation and observation share the same launchd machine scope.

The scope is read from ops/config/services.json, where every launchd
environment declares runs_on: "primary", "secondary" or "every". This module
derives PRIMARY_ONLY and SECONDARY_ONLY from it, so the installer, the
scheduled-job inventory and the machine-sync audit cannot hold a different
list from the catalog (rule a8c55a47: one job, one code path).
"""
import json
import os

CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "ops", "config", "services.json")
RUNS_ON = ("primary", "secondary", "every")

# WHY A JOB IS runs_on "primary": it RUNS ON EXACTLY ONE MACHINE. Not a
# statement about Joe; a statement about what the job writes. Each of these
# mutates state that is SHARED between the two machines, so a second copy is
# either duplicated work or a two-writer conflict.
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
# Joe 2026-09-26: the Mac Studio is the hub and the MacBook is a thin client
# into it, so work that acts on shared state runs on the primary alone.
# room-bridge: both Macs carried the same Model Room desks and raced for each
# turn; it also wakes the engineering controller, whose one Worker token lives
# on the primary. release-pipeline, merge-queue and control-plane-tick would
# release and enqueue twice. The cadence sweep would escalate twice.
# nightly-exports-daytime-retry is the safety net for nightly-record-layer.
# timebomb-audit scans the same tracked source on every Mac. Device-bound jobs
# (dictation, call mode, capture, keymap, local servers, spool flush, fleet
# sync) are runs_on "every". tailscale-up (WR-000178): the Studio's Tailscale
# stayed stopped ~6h after the 2026-09-30 reboot and cut SSH to the MacBook;
# the hub is the node that must come up.
#
# What the second machine still needs from the nightly is the record-derived
# fetch allowlist, which is per-machine and gitignored. That is why
# com.carr.fetch-allowlist.plist exists as its own job rather than being
# inherited from the nightly chain.


def placements(catalog: str = CATALOG) -> dict[str, str]:
    """{plist filename: runs_on} for every launchd environment in the catalog.

    A launchd environment without a valid runs_on, or two services placing the
    same plist differently, is refused rather than defaulted: a guessed
    placement is how a shared-state job ends up running on both Macs.
    """
    with open(catalog, encoding="utf-8") as source:
        services = json.load(source)["services"]
    placed: dict[str, str] = {}
    for service in services:
        for environment in service.get("environments", []):
            mechanism = environment.get("deploy_mechanism") or ""
            if not (mechanism.startswith("ops/launchd/") and mechanism.endswith(".plist")):
                continue
            where = environment.get("runs_on")
            if where not in RUNS_ON:
                raise ValueError(f"{service['key']}:{environment.get('environment')} declares "
                                 f"runs_on={where!r}; expected one of {', '.join(RUNS_ON)}")
            name = os.path.basename(mechanism)
            if placed.setdefault(name, where) != where:
                raise ValueError(f"{name} is placed both {placed[name]!r} and {where!r}")
    return placed


PLACEMENT = placements()
PRIMARY_ONLY = frozenset(name for name, where in PLACEMENT.items() if where == "primary")
# The mirror image: jobs only the SECOND machine needs, because the primary
# already gets the same effect from a chain the second machine must not run.
SECONDARY_ONLY = frozenset(name for name, where in PLACEMENT.items() if where == "secondary")


def allowed_on_machine(filename: str, primary: bool) -> bool:
    return not ((filename in PRIMARY_ONLY and not primary)
                or (filename in SECONDARY_ONLY and primary))
