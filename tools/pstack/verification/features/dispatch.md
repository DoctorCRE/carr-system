# Dispatch cards

Allow registered Card-bearing launches and refuse invalid or absent registration.

## Sub-features

- dispatch.admission exercises valid input.
- dispatch.refusal exercises invalid input and protects unchanged state.

## How to get to it (user POV)

Use agent dispatch through T3 or the Codex CLI.

## Driving it with the repository CLI

Preconditions: isolated checkout, Python available, Doctor passes.

Run `python3 ops/agent-card-gate-selftest.py`. Require exit zero and all assertions passing. Capture stdout and stderr with the source SHA in a unique feature receipt directory. The selftest drives the command boundary with synthetic inputs.

## Gotchas

Registration fixtures never mint real Work Requests.
