# Why the clean-week gate cannot reach zero — the cause split

Measured 2026-08-27 ~15:20Z against `out/rule-delivery-shadow.jsonl`, epoch
opened 2026-08-27T10:56:44Z. This answers the first acceptance criterion of
WR-000027 ("the clean-week gate for the rule-delivery flip cannot go clean on
its current trajectory"), which asked for the two suspected causes to be
separated by measurement before anything is changed.

## The population

57 observations under the current epoch. **All 57 carry at least one missing
pack**, so every observation is a finding. 13 of the 57 declared any pack at
all. Findings close only by an explicit per-event disposition of "explained"
or "remediated"; there is no path by which a run of clean sessions clears
them, which is the difference between this ledger and the scheduled-job
incident ledger that gained automatic success-clears in PR #568.

## Method

For each missing pack on each observation, the recorded `triggers` say which
keywords fired it. A pack is counted **strongly evidenced** when two or more
distinct keywords fired it, or when a keyword names the pack's own subject
(`git`/`commit`/`migration` for engineering, `rule`/`doctrine`/`decision` for
governance, and so on). A pack fired by a single generic word is counted
**weakly evidenced** — the signal that the work truly entered that pack is
thin.

The split is deliberately generous to the over-fire hypothesis: anything with
two keywords counts as real, so genuine drift is more likely to be classed as
a declaration gap than the reverse.

## Result: the two causes are not comparable in size

| classification of finding | count | share |
|---|---:|---:|
| contains a genuinely-needed undeclared pack (strong evidence) | 55 | 96% |
| pure trigger over-fire, no strongly-evidenced pack at all | 2 | 4% |

Of the 55, six are clean declaration gaps with no noise attached, and 49 are
mixed — a real gap with over-fire riding on top of it.

**The dominant cause is that sessions do not declare their packs.** The
detector does over-fire, but that noise almost never creates a finding by
itself; it rides along on findings that a declaration would have been needed
for anyway.

Two packs are never spurious: `governance-rules` fired strongly 49 times and
weakly zero times, `engineering-git` 43 and zero. Any session working in this
repository is genuinely in both.

## But neither remedy alone gets close to zero

Counterfactuals over the same 57 findings:

| remedy | findings remaining |
|---|---:|
| sessions declare the packs their work is really in | 51 of 57 |
| trim the 11 noisiest keyword-to-pack mappings | 55 of 57 |
| **both together** | **16 of 57** |

Declaration alone barely moves the number, because a session that correctly
declares its real packs still gets charged for the spurious ones the detector
named. Trimming alone barely moves it either, because the real gap underneath
remains. Together they clear roughly 72%, and **16 findings still survive** —
so a third step is required before any epoch can go clean, whether that is
further trigger work or an explicit batch disposition path for the residue.

This is the part worth carrying forward: the fix is not one change, and the
clock should not be restarted until whatever is chosen has been shown to
produce actual clean observations.

## The noisiest mappings

Single words that pulled in a whole pack on their own:

| times | word | pack it pulled in |
|---:|---|---|
| 23 | merge | records-intake |
| 16 | ledger | joe-development |
| 14 | source | source-study |
| 11 | post | joe-comms |
| 6 | repo | source-study |
| 5 | artifact | surface-doctrine |
| 5 | seat | delegation-council |
| 5 | design | surface-doctrine |
| 4 | workflow | delegation-council |
| 3 | job | scheduled-automation |
| 3 | credential | scheduled-automation |

`merge` is the standout: in a repository whose normal working vocabulary is
pull requests and merges, it pulls in the records-intake pack 23 times.

## What this does not settle

Separately from the bookkeeping, zero clean observations in four hours is the
shadow mechanism doing its job and reporting that the scoped selector would
have omitted needed rules in essentially every turn measured. That verdict is
about whether the selector is safe to enforce, and it is not answered by
fixing the finding counter.
