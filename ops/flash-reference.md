# Flash reference

Flash is Qwen3.8-Flash-Next Q2 on the Studio's ds4 server, loopback port 8000.
Start it only for Joe's explicit request. Consumers and health probes must never
start it. Preserve context 131072, prefill chunk 1024, MTP and disk-KV settings
until a smaller setup is tested. The recorded planned footprint was 47.33 GiB.
The server plist and existing explicit activation/shutdown controls remain.
This source deletion does not change installed launchd disabled state.

Give the model small precise jobs with independent checks. Call extraction uses
thinking off, temperature 0, 2000 output tokens, a 150-second request deadline,
an exact JSON example, allowed IDs and local validation. Schema acceptance did
not guarantee compliance. Keep uncertainty as review questions. Reject blank
visible replies; request one visible restatement when justified rather than
extracting a reasoning draft. Keep Jev's narrow evidence questions on its
official SDK, with abstention and unavailable outcomes.

For coding, use the normal coding agent and actual tests. Feed failures into
bounded retries. Thinking off was faster for a first simple attempt. Keep stable
prefixes and send only needed context. For data, inspect anomalies, normalize
records, check coverage, batch semantic questions and compute totals in code.
Printed numbers and model confidence do not prove complete or correct extraction.
The script lab missed real records and clauses despite plausible counts and Jev
scores. More planning and one semantic call per phrase also performed worse.

The detailed evidence archive is the supplied Flash knowledge report (not kept in this
repository). Its results are historical observations, not fresh benchmarks. Exact
[scorecard tasks](https://github.com/DoctorCRE/carr-system/blob/c9ab378856ace1bb30d94d962eb3e016e9daeb72/ops/config/flash-scorecard-tasks.v1.json)
remain in git. Hard cases included chained arithmetic, a Dijkstra node named
`None`, incomplete SemVer suffixes and leap-year boundaries. See the
[thinking/cache experiments](https://github.com/DoctorCRE/carr-system/pull/1184),
[prompt reduction](https://github.com/DoctorCRE/carr-system/pull/1191), and
[script lab failures](https://github.com/DoctorCRE/carr-system/pull/1250).
Jev's separate evidence policy scored 16/16 before a generic confidence fallback
reduced it to 13/16; two raw successes were abstentions handled by fallback.
Do not turn that result into a universal confidence gate or accuracy claim.
