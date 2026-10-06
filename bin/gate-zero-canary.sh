#!/bin/zsh
# gate-zero-canary.sh — the no-op job whose only product is proof that the
# scheduler ran it.
#
# WHAT IT IS FOR. Gate Zero's fourth predecessor step,
# `step:scheduler-active-receipt`, asks a question no ordinary job answers:
# is the scheduler REALLY running? The reader that judges it —
# mcp-server/src/gate-zero-seam-readers.v5.js, `deriveSchedulerCanary` — wants
# three clauses out of the Control Plane ledger:
#
#   receipt_binding             the dispatch row carries a non-null
#                               ops.run.evidence_ref AND was written by
#                               bin/run-scheduled.sh with source_kind wrapper,
#                               and that receipt names THIS run key and was
#                               minted after this row's started_at
#   canary_match                the observation is of THIS run — same run_key,
#                               same evidence_ref
#   observation_after_dispatch  observed_at is STRICTLY after started_at
#
# THIS SCRIPT SUPPLIES NONE OF THAT, AND THAT IS THE WHOLE DESIGN. The receipt
# is minted by bin/run-scheduled.sh, out of the wrapper's own clock and the
# machine's own entropy, AFTER this child exits — there is no flag that hands
# the wrapper a path and no file this job could write that would reach
# ops.run.evidence_ref. An earlier draft of this file took a receipt path as
# $1 and wrote a token into it, against the `--evidence-ref-file` interface the
# wrapper carried before PR #1002. That interface is gone: the wrapper's option
# loop recognises `--heartbeat-interval` and `--also-heartbeat` and treats
# anything else as the START OF THE POSITIONAL ARGUMENTS, so a retired flag is
# not rejected — it is silently read as the service key, and the job the
# scheduler then tries to execute is whatever word followed the run key. A
# canary wired that way misfires quietly, which is the one failure mode a
# canary must not have.
#
# WHY A DEDICATED JOB RATHER THAN WATCHING A REAL ONE. A real job's row mixes
# two facts — "the scheduler fired" and "the work succeeded" — and a red row
# cannot be read for the first without knowing the second. This job has no
# work to fail at, so its row is a clean statement about the scheduler alone.
#
# WHY IT IS THE SMALLEST THING THAT CAN BE WRITTEN. It touches no database, no
# network, no record layer and no file. It takes no arguments and it prints
# nothing: the wrapper's own log line and the ops.run row it records are the
# only trace this job's existence is supposed to leave. Anything else it did
# would be something else that could break, and a canary that can fail for its
# own reasons is a second job to debug rather than a signal.
#
# MEASURED 2026-09-11, against production, and it is why this file exists:
# 28,309 rows in ops.run, 21,894 of them written by the wrapper, and ZERO
# carrying an evidence_ref. Not one run in the ledger's history could have
# satisfied `receipt_binding`, canary or otherwise.
#
#   usage: bin/gate-zero-canary.sh
#
# It is invoked through the wrapper, with no options at all — the wrapper mints
# the receipt for itself, so there is nothing to pass it:
#
#   bin/run-scheduled.sh gate-zero-canary gatezero.canary \
#     /bin/zsh {{REPO}}/bin/gate-zero-canary.sh
#
# ARGUMENTS ARE IGNORED RATHER THAN REFUSED, deliberately. A canary that exits
# nonzero because somebody typed an extra word records a FAILED run, and a
# failed canary row says "the scheduler is broken" about a scheduler that just
# demonstrated it works. There is nothing here for an argument to change.
#
# EXIT CODE. Always 0. ops/gate-zero-scheduler-canary-gate.py runs this file
# through the real wrapper against a disposable Control Plane ledger and reads
# the resulting row back through the real card-12 reader, so the contract above
# is proven end to end on every push rather than asserted in this comment.

exit 0
