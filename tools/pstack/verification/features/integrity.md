# Gate integrity

Reject changed safety hooks and missing configuration targets.

## Sub-features

- integrity.admission exercises valid input.
- integrity.refusal exercises invalid input and protects unchanged state.

## How to get to it (user POV)

Run python3 hooks/gate-integrity.py --strict before delivery.

## Driving it with the repository CLI

Preconditions: isolated checkout, Python available, Doctor passes.

Run `python3 ops/gate-integrity-selftest.py`. Require exit zero and all assertions passing. Capture stdout and stderr with the source SHA in a unique feature receipt directory. The selftest drives the command boundary with synthetic inputs.

## Gotchas

Do not bless drift unless the task intentionally changes the named hook.
