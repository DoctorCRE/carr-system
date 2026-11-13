# Design

The shared premise was that a 500 ms whole-CLI deadline leaves enough time for receipt parsing, release HTTP, three Git subprocesses, Python startup, and descendant creation. It does not.

## Evidence before implementation

Original job logs fetched via one successful `gh api .../jobs/<id>/logs` each.
- PR 3068, run 42387407678, original job 128447732272. Subtest 3 failed reading child.pid with ENOENT after 1423.553654 ms. The preceding exit-78 and elapsed <2000 assertions passed.
- PR 3077, run 42508296764, job 128815850437. Subtest 2, not subtest 3, failed reading child.pid with ENOENT after 1678.555789 ms. Subtest 3 passed. This corrects the briefing's attribution. Logs are /tmp/J257-3068.log and /tmp/J257-3077.log.
- Baseline under 18 busy workers passed. Under 72 busy workers it also passed. Saturation alone did not reproduce the hosted schedule.
- Controlled scheduling reproduction under 72 owned busy workers delayed fixture startup by 600 ms before Popen. Both descendant modes failed at child.pid with ENOENT. Runtime cleanup was unchanged. Temporary instrumentation was restored before implementation. /tmp/J257-load-baseline.log.

The exact setup assumption fails at `pid = Number(await readFile(join(dir, "child.pid"), "utf8"))`. The 500 ms deadline starts before release and Git reads. Production correctly exits 78 when that budget expires before a descendant exists. This is a test synchronization failure caused by scheduling against the total deadline, not evidence of broken killpg, reaping order, or surviving processes. The separate elapsed <2000 assertion adds another scheduler dependency.

## Requirements

Keep real CLI behavior and deadline budget unchanged. Establish real descendants before expiry. Exercise expiry during communicate, after partial stdout with inherited pipes still open, and while Popen acquisition has not returned. Assert exit 78, total_deadline, readiness, and no surviving descendant. Hang watchdogs must fail and clean up, never pass. Test removed cleanup. Run 50 consecutive repetitions under owned all-core load. Reject elapsed-time assertions in descendant proof tests. Use only checked-out files and shallow-clone validation.

## Alternatives

1. Readiness pipe plus real timer, killpg and waitpid. Low implementation cost; bounded acquisition remains vulnerable to scheduler latency unless the timer starts after readiness. That changes the scenario and would need a production seam. waitpid confirms only children unless a Linux subreaper adopts grandchildren.
2. Readiness pipe plus injected monotonic clock and real SIGALRM. Small test-only adapter; real Popen, sessions, signal masking, pipes and killpg remain active. Freeze logical time through setup, move it to the deadline and deliver the production signal at the selected phase. Risk is adapter drift; assert exact readiness/partial output, exit reason, pending-launch marker, descendant death, and a cleanup-disabled mutation. Keep ordinary CLI timer timeout coverage separately. Recommended.
3. Separate Linux subreaper integration harness. Deterministic adoption and waitpid proof of SIGKILL, with kernel lifecycle events. Medium platform-specific cost; Linux-only and needs a separate portable path for macOS. Does not itself remove startup-versus-deadline races without synchronization.

## Sources

- POSIX setsid specifies a new process group/session; kill with a negative PID targets that group. https://pubs.opengroup.org/onlinepubs/9799919799/functions/setsid.html and https://pubs.opengroup.org/onlinepubs/9799919799/functions/kill.html. Open Group denied browser retrieval (403); not quoted. Python's primary documentation independently confirms start_new_session calls setsid and explains creation latency.
- https://docs.python.org/3/library/subprocess.html. Creation can outlast a timeout; start_new_session calls setsid; communicate timeout does not itself kill the subprocess.
- https://nodejs.org/api/child_process.html#optionsdetached. Detached children on POSIX lead a new session and process group. Keep the Node launcher attached; production Python already isolates acquired children.
- https://engineering.salesforce.com/flaky-tests-and-how-to-avoid-them-25b84b756f60/. Practitioner account recommends condition waits and explicit synchronization for assumed operation ordering, rather than fixed sleep assertions.

## System design

Test module owns synthetic receipt and registered-response fixtures. Python test adapter owns injected logical time, acquisition-phase scheduling and a watchdog. Production module retains ownership and cleanup of acquired process groups. No production interface changes. At current and 10x test load, startup latency may increase but cannot consume logical deadline before readiness; watchdog capacity remains the outer infrastructure limit. No network external calls, storage, caching, database, N+1, consistency, queues, rate limits, idempotency keys or production circuit breakers apply. Local fixture HTTP and processes are bounded by the suite and watchdog. Worker load has a finite repetition cap and only recorded owned PIDs are killed.

## State transitions

Setup -> Ready (readiness line after child.pid creation).
Ready -> Communicating -> Expired (descendant).
Ready -> PartialOutput -> Expired (late-descendant).
Ready -> AcquisitionPending -> Expired -> Acquired (late-launch, SIGALRM blocked by production).
Expired/Acquired -> Cleaned -> Exit78 (real finally killpg and wait).
Any stuck state -> WatchdogFailure (owned groups killed; exit124).
Never accept Exit78 without readiness and descendant death; never pass after watchdog cleanup.

## Enforcement and slices

One PR. Replace startup sleeps/elapsed assertions with readiness-driven injected expiry. Add a test guard against wall-clock assertion dataflow in proof files covering descendants. Preserve the separate real-clock timeout test. Execute affected suite, cleanup-disabled mutation, 50 runs under owned load, required CI classes, depth-1 pushed-branch clone, and one final hosted CI read. Production proof remains an observation after coordinator merge, on main and the next three PRs containing this change.

## Three repositories

DoctorCRE/carr-system owns the failing identity proof test and receives this change.
The read-only local doctorcre-app test tree has no descendant-cleanup proof test matching `descendant|killpg|process.tree`. That checkout predates the hosted failures, so this is bounded local evidence, not a claim about current remote main.
The read-only local software-factory checkout has a related `hard timeout kills grandchildren that hold inherited pipes open` process-runner test with `assert.ok(Date.now() - start < 3000)`, and a `waitGone` polling helper. It tests orchestration, rather than this production proof CLI. No changes are authorized there by this brief; report the analogous risk to the coordinator.
