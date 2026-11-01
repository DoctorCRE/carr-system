# Orchestrator rotation

Opt in with the first non-meta prompt marker, rotate once at threshold, and preserve worker sessions.

## Sub-features

- rotation.admission exercises valid input.
- rotation.refusal exercises invalid input and protects unchanged state.

## How to get to it (user POV)

Start an orchestrator with CARR-Session-Role: orchestrator as an exact prompt line.

## Driving it with the repository CLI

Preconditions: isolated checkout, Python available, Doctor passes.

Run `python3 ops/orchestrator-rotation-selftest.py`. Require exit zero and all assertions passing. Capture stdout and stderr with the source SHA in a unique feature receipt directory. The selftest drives the command boundary with synthetic inputs.

## Gotchas

The test owns temporary transcripts and HOME. It never launches a successor.
