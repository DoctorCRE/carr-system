# Post-deploy production proof requirements

Production proof is the coordinator's after-deploy step. PR descriptions have no
production-proof admission check and need no Production proof section.

`ops/delivery-plan.py` reads landed requirements through `ops/delivery_plan.py`.
That resolver still reads `contracts/production-proof-plan.v1.json` for requirement
validation, target classification and the pinned observer registry. Keep this
contract and the requirements for post-deploy execution. No PR command is executed.
Review checks the declared predicate's relevance. A declaration alone never proves
that a feature works.

## Studio run observations

Declare `studio` and `studio-run-receipt.v1` for installed Outlook runtime changes.
The contract lists paths that require this target even if a declaration names
only worker. Development tooling has explicit non-product entries;
`ops/ci-dispatch.py` and other installed operations remain product paths.

The Studio adapter reads one explicit service receipt file. It never starts a
job, refreshes the runtime, discovers personal files, sends, or writes records.
Its command interface is `python3 ops/studio-proof-observe.py --receipt-file FILE
--service SERVICE --runtime-sha FULL_SHA --count COUNT_PATH --minimum INTEGER`.
`COUNT_PATH` selects a dot-separated key under `counts`, such as `run.processed`.
Use the pinned source revision from the registry when executing a declaration.
The assertion predicate names the receipt source, count and minimum. Review
checks those choices against the changed behavior. A minimum of zero proves
only the declared idle predicate, never a nonempty processing path.

A landed Studio declaration can exist before its receipt.
Execution then reports BLOCKED for a missing receipt, a failed run, insufficient
counts, or a mismatched full runtime SHA. Current receipts with abbreviated
SHAs remain blocked until full identity is available. This adapter does not
supply P7 collection, trusted provenance, receipt ingestion or completion truth.
A successful local comparison alone cannot establish those facts.
