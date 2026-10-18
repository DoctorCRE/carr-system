# Retrieval eval from real past misses

This is step 1 of the link-and-context layer plan. It measures current reads before any edge table, traversal, vector search, or generated trigger change. It changes no schema or product behavior.

## Run the baseline

From the repository root, use the existing authenticated local verb door:

```sh
python3 evals/retrieval-real-misses/run_eval.py --output /tmp/retrieval-real-misses.json
```

The runner needs the same local identity as `./run.sh call`. It invokes only an explicit allowlist of read verbs. It never opens a database connection or invokes a mutation verb. Repeating the run changes only its local output file. Existing server read-audit metadata and optional doctrine-search telemetry still occur; this eval neither adds nor disables them.

The runner captures child stdout and stderr, reports fixed error categories, and saves only record refs, eligibility, and scoring metadata. It never saves names, notes, contact details, or credentials. Doctor queries resolve names in memory from fixture refs through the current Leads workspace or Deal Board. Deal IDs resolve only for returned rows, using an unambiguous exact `(client_ref, name)` lookup in the live Deal Board. A deal appearing solely on that board cannot earn a retrieval hit.

Rescore the committed observations without production access:

```sh
python3 evals/retrieval-real-misses/run_eval.py --replay /tmp/retrieval-real-misses.json --output /tmp/retrieval-real-misses-replay.json
```

An errored read stays in the denominator and scores no hit. Any read error also makes the runner exit 1. Invalid fixtures or replay inputs exit 2. An ordinary measured miss does not fail execution.

## Baseline on October 6, 2026

The recorded run completed at `2026-10-06T14:44:41.654417+00:00`. All read calls completed. The live-versus-retired assertions passed.

| Cause | Top-five hits | Questions | Hit rate |
| --- | ---: | ---: | ---: |
| never-asked | 7 | 9 | 77.8% |
| never-asked/drift | 3 | 3 | 100.0% |
| search-quality | 4 | 9 | 44.4% |
| not-linked | 2 | 3 | 66.7% |
| not-captured, gap locator | 0 | 3 | 0.0% |
| Overall retrieval | 16 | 27 | 59.3% |


The remaining three questions measure unordered rule availability. Two needed rules were available through the full-text boot or matched deterministic routes, for delivery recall of **2/3, 66.7%**. The stale-notes rule missed that probe. Rule IDs have no relevance ranking, so these questions do not enter the top-five denominator.

This is a purposive regression set with several related phrasings per historical case. It estimates neither general user-question recall nor a quality improvement. Historical migrations have repaired some of the original failures. The observed numbers describe today's post-repair corpus and verbs. There is no candidate implementation or shipping-quality verdict in this step.

The not-linked questions measure whether a targeted lookup reaches the overturning ruling. They do not measure whether reading the dated memo automatically brings that ruling into context. The not-captured questions measure gap-locator reachability, not conversation recovery.

## Scoring contract

[questions.v1.json](questions.v1.json) contains thirty questions derived from the ten cited cases. `sources` cites migration headers, source comments, current production loops and rules, and the historical synthetic experiment. [resolved-refs.json](resolved-refs.json) records the independent read verbs used to verify every expected ref in production on October 6. Expected labels are fixed independently of baseline responses.

A retrieval question declares one result lane. The runner takes that lane's first five endpoint rows, preserving their returned order and duplicate positions. A hit means any expected ref appears in an eligible row. Ineligible rows still consume positions. One organization row can carry several live refs. Names, snippets, input refs, retired aliases, and unrelated context links cannot earn hits. This is a lane-specific hit rate because `find` returns separately ordered arrays and provides no global relevance ranking across them.

Organization cases also assert live counts and forbid retired refs as live targets. These contract assertions are separate from identity retrieval. A response can locate the survivor and still fail its live-count assertion. Doctrine results use section UUIDs and preserve the fallback flag. Precedent results use decision UUIDs and exclude typed precedents from settled-ruling hits.

Rule probes read the full-text boot and reuse the existing deterministic prompt matcher and tool/path route matcher. They ask `standing-context` for the matched IDs and intersect its delivered full text with those IDs. Unrequested rules in that response cannot earn trigger delivery. Boot index summaries cannot earn full-text availability. The probe does not run Jev, session deduplication, or hook context fitting, so it measures deterministic availability rather than end-to-end semantic rule delivery.

The uncaptured orb conversation has no production conversation ID to label. Those questions target the existing loop as a gap/work locator. Even a future locator hit would not prove recovery of the original conversation. Its absence is a capture limitation, and expanding the search cannot reconstruct missing source material.

The old **12/23, 52.2%** result belongs to the synthetic strict-FTS reference committed in [72bac3e9](https://github.com/jbookout/carr-system/commit/72bac3e9abc09a5a8e908cefeae0e692c9ec73a5). Its own `basis` says it was not live recall. [PR 1546](https://github.com/jbookout/carr-system/pull/1546) removed that unused experiment. This fixture rebinds its application-placement question theme to an independently read production doctrine section. It copies no synthetic UUID into an expected label.

## Verification and registration

Run the scorer's behavioral tests:

```sh
python3 tools/test-retrieval-real-misses.py
```

The tests cover rank five versus six, tombstones, duplicate rank positions, exact refs, error denominators, live-count drift, settled versus typed precedent, query hydration, omitted linked deals, boot versus trigger availability, privacy, and separate routing recall. The fixture oracle scores 100% and the null observation scores 0% for both retrieval and routing. Repeated scoring agrees exactly. The test file lives under `tools/` so the existing `ops/ci.sh` discovery collects it without a CI-script change.
