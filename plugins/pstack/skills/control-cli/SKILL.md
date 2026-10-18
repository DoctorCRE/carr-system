---
name: control-cli
description: Drive a real CLI or TUI in an isolated terminal session. Use to reproduce terminal defects or verify commands, prompts, keyboard input, output, exit status, and side effects.
---

# Control a CLI

Read [the port contract](../../PORT.md) and [the model routes](../../pstack-models.md) first.
This local dependency replaces pstack's reference to `cursor-team-kit`'s `control-cli`.

## Launch and doctor

Read the repository's CLI entrypoint, documented commands, tests, and existing PTY or expect helpers.
Build or install through the repository's supported command when needed.
Inspect the CLI's help and version without running a mutating command.
Record the executable, revision, working directory, arguments, and fixture state.

Use an isolated temporary working directory and run-owned data paths where the CLI supports them.
Preserve the user's terminal session, configuration, and data.
For an interactive CLI or TUI, allocate a PTY through the available terminal tool or existing harness.
Record its session handle and terminal dimensions. A piped process does not prove interactive behavior.

## Send input and observe output

1. Capture the initial prompt or screen. For a bug fix, reproduce the failure before changing the code.
2. Send the documented command or keyboard input to the owned session.
3. Capture output after each meaningful action. Preserve the input sequence and output order.
4. Verify the resulting prompt, screen, stdout, stderr, and exit status against the expected behavior.
5. Read any relevant files or other authorized side effects after the action.

For a TUI, exercise its keyboard path, including the relevant navigation and quit action.
Send control keys through the terminal tool's documented API. Inspect the screen after each state change.
Use separate PTYs and fixture directories for concurrent runs.
If a command has a dry-run mode, inspect what it changes instead of trusting the flag's name.

## Evidence, reset, and cleanup

Save the terminal transcript, input sequence, dimensions, exit code, and side-effect readback in a run-specific artifact directory.
Capture a terminal screenshot or recording when screen layout or animation is part of the claim.
Inspect the saved evidence before reporting success.

Reset only the fixture state created by this run through the CLI's supported path.
Exit through the CLI's quit command first. If needed, terminate only the recorded owned process or session.
Run cleanup after a failed attempt too. Preserve the transcript and other proof artifacts.
Report the result, evidence paths, and any behavior the terminal route could not verify.
