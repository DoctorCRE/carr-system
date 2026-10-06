---
name: control-ui
description: Drive a browser or Electron UI through its real user path with Playwright. Use to reproduce a UI defect or verify changed behavior with observed selectors and captured evidence.
---

# Control a UI

Read [the port contract](../../PORT.md) and [the model routes](../../pstack-models.md) first.
This local dependency replaces pstack's reference to `cursor-team-kit`'s `control-ui`.

## Launch

Read the target repository's run commands, Playwright configuration, and existing end-to-end tests.
Use its existing Playwright harness first. DoctorCRE's application repository has end-to-end tests to inspect.
If none covers the journey, extend the project's harness within the authorized source scope.
Read the installed Playwright API before using a method whose contract is uncertain.

Start the app with its documented verification command and record the process handle, URL, and readiness signal.
Use a run-owned browser context and page with isolated storage and test data.
For Electron, use the repository's Electron launch harness and its owned window.
If isolation is unavailable, report that limit before driving a shared instance.

## Doctor

Confirm that the instance responds, belongs to this run, and serves the intended build.
Inspect the initial page, route, authentication state, and required fixture data.
Run this read-only check again when the page or instance behaves unexpectedly.

## Act and observe state

1. Capture the initial UI state. For a bug fix, reproduce the reported failure before changing the code.
2. Read the rendered DOM or accessibility state to choose selectors.
3. Prefer observed roles, accessible names, labels, and stable test IDs. Use coordinates only when the interface exposes no usable selector.
4. Exercise the user journey through visible controls. Wait for the resulting state with the harness's assertions.
5. Verify the visible result and any relevant side effect through an authorized readback.

Keep each concurrent run in its own context and page. Do not use internal setters or test-only endpoints to substitute for the user action.
Use mocks only at an existing external-system boundary. Name any boundary the run did not exercise.

## Evidence

Save the action trace, assertions, initial state, and resulting state in a run-specific artifact directory.
Capture screenshots of the relevant states. Enable Playwright video or tracing when the journey needs motion or action history.
Record the tested revision, command, URL, fixture, and expected result alongside the artifacts.
Inspect the artifacts before claiming success. A final screenshot alone does not prove the action or persistence.

## Reset and cleanup

Reset only this run's test state through the repository's supported fixture or UI route.
Close the owned page, context, and browser. Stop only the app process this run started.
Run cleanup after a failed attempt too. Keep screenshots, video, traces, and logs after cleanup.
Report the result, evidence paths, and any unverified behavior.
