# Read capacity before PostgreSQL verification

CARR's provisional Studio envelope is eight jobs, nine clusters per job, sixteen background slots and eight uncertainty slots. The versioned boot profile is [studio-sysv.conf](../../../ops/config/studio-sysv.conf). It sets SHMMNI 256 and SHMALL 65536. The profile test locks those floors to the reader.

Run the read-only preflight with `python3 lib/pg_capacity.py`. It returns JSON and exits 0 for sufficient capacity, 1 for insufficient capacity, or 78 for unreadable evidence. Each host command has a five-second deadline. No retry, host write or resource reclamation occurs. Canonical CI checks capacity before unit, gates or migration work. The local PostgreSQL CLI checks before creating its primary cluster. The Node runner checks before building PostgreSQL snapshot templates.

The check separately compares profile floors and current occupancy plus the entire declared fleet and uncertainty reserve. It deliberately counts already-running job clusters as background. It is conservative and does not reserve capacity. Other processes can consume resources after the read. Passing this check does not authorize raising the four-job cap or establish eight-job operational acceptance.

On Darwin, SHMALL accounting uses 4096-byte units. Allocation rounding also matters. The reader rounds each segment to the larger of hardware pages and accounting pages before counting committed units. This conservatively covers the XNU allocation rounding shown in [sysv_shm.c](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/sysv_shm.c). [PostgreSQL's macOS guidance](https://www.postgresql.org/docs/18/kernel-resources.html) documents 4 KiB SHMALL units and recommends all five boot parameters. Linux reads its proc SysV table and kernel limits with native page accounting.

## Owned lifecycle evidence

Set `CARR_PG_TELEMETRY` to an owned JSONL path and `CARR_PG_JOB_ID` to the job card. The existing owning supervisor appends initdb, start and stop begin/end/failure events. Each event includes UTC timestamp, owned root and data paths, supervisor PID, observed postmaster PIDs, pinned memory backends, capacity limits and occupancy. Segment attribution matches the creator PID read from an owned postmaster.pid. Stop events retain that PID correlation after root removal. They report observed segments rather than inferring teardown from a shared-host count.

Telemetry is opt-in. It emits no environment values beyond the explicitly named job identity. It never changes capacity or cleanup policy. A telemetry read or sink failure prints a diagnostic. Startup diagnostics distinguish shared-memory ENOSPC and ENOMEM and retain bounded owned log evidence. Initdb begin/end events bound bootstrap time, but do not continuously sample its transient internal postmasters. A killed supervisor can leave an unmatched interval. Missing telemetry is insufficient evidence for a peak claim.

Aggregate overlapping successful start-to-stop intervals to measure live clusters and hold times. Keep bootstrap intervals separate. Do not count stopped templates or mocks as live servers. A focused workload supplies focused evidence only. A complete representative workload, eight-job overlap proof and reboot readback remain required before replacing the provisional nine-cluster envelope.

## Model and tests

[preflight.mmd](preflight.mmd) has independent host-outcome and verification states. Sufficient permits running. Insufficient and unreadable refuse. There is no retry transition. The model excludes host activity after the snapshot, process admission and lifecycle telemetry.

The transitions map to `test_sufficient_continues_with_declared_eight_job_envelope`, `test_insufficient_reports_limits_and_separate_occupancy`, `test_unreadable_fails_once_without_retry` and `test_ci_refuses_before_controller_for_every_expensive_class` in `tools/test-pg-capacity.py`. The Node refusal regression executes the real runner with a fault-injected capacity subprocess before snapshot acquisition.

Regenerate the chart in a scratch directory with the pstack statechart skill's `from-chart` command. TLC checks InsufficientStarts, UnreadableStarts and UncheckedStarts. The checked-in chart has four reachable combined states and three transitions.

## Boot profile qualification

The Studio was observed on macOS 27.0.1, build 26A434. No boot-file qualification has been executed on this release. The supplied admin procedure uses PostgreSQL's documented macOS boot path and exactly one reboot. Read actual sysctls afterward. If the release ignores the file, record that concrete result and return to the approved design's container alternative. Do not add late boot-order scripts.
