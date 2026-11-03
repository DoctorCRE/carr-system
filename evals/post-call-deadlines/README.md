# Post-call deadline policy evaluation

Run from the repository root with the locked Python dependencies installed.

```
python3 evals/post-call-deadlines/score.py --output /tmp/deadlines-head.json
python3 evals/post-call-deadlines/score.py --revision origin/main --output /tmp/deadlines-main.json
python3 evals/post-call-deadlines/score.py --revision eee12711eed810f43a2cbea10e52db19c5b31ff5 --output /tmp/deadlines-prior.json
```

The scorer executes each revision's real `post_call.py`, with the same unchanged
coverage implementation, synthetic context and transcript, authored extraction
and history responses, and offline `rehearsal_jev.py` transport. It checks the
actual report, coverage binding, review_complete and retention_ready after a
simulated candidate confirmation. It sends nothing and uses no real recordings.
Logs and budgets are redirected to temporary files by the offline suite harness.
Older revisions receive the same task response minus the new timing field,
which their exact schema cannot accept. The corpus and expected outcomes remain
identical. A baseline failure exits 1; results still contain every case.

The 55 JSONL cases contain the 33 J86 probes, all 14 J84 retained probes, and
eight additional synthetic cases. They cover property numbers and non-temporal
words, EOD/COB, foreign-language timing, holidays, unsupported dates, relative
and calendar dates, corrections, separate speakers, alternatives, no timing,
uncertainty, year rollover and invalid calendar dates. Each case stores the
full authored timing response beside its expected filing, date and reminder.

The rubric is exact equality on three independent outcomes per case: filing and
retention eligibility, the resolved calendar date, and optional-reminder presence.
Unsafe eligibility and unnecessary holds are also reported separately. These
counts describe this selected regression corpus, not real-call error rates.

## Measured comparison

| Revision | Exact cases | Unsafe eligibility | Unnecessary holds |
| --- | ---: | ---: | ---: |
| main `8c03b8a8f` | 35/55 | 2 | 18 |
| prior `eee12711` | 28/55 | 15 | 12 |
| structured timing | 55/55 | 0 | 0 |

All model-output parts are authored stubs. No model was run or judged. This
measures the deterministic handling of supplied semantic judgments and exact
source integrity, not whether a live extractor reliably classifies an unseen
utterance or includes every correction. A false semantic `unstated` assertion
can still be wrong despite perfectly matching source bytes. That uncertainty
requires extraction-quality evaluation with authorized model runs before a
model-accuracy claim. Missing or explicitly uncertain judgments fail closed.

The [statechart](../../specs/post-call-deadline-states.md) names transition tests.
Unit tests separately mutate source quotes, hashes, speakers, offsets, omitted
turns and uncertain judgments. Full quotes are evidence, so the summary-copy
check excludes task timing fields; copied narrative summaries still hold.
