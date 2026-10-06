#!/usr/bin/env node
// recovery-matrix-evaluate.mjs — evaluate V5-F08 recovery evidence from JSON.
//
//   node mcp-server/bin/recovery-matrix-evaluate.mjs restore  <restore-exercise-receipt.json | ->
//   node mcp-server/bin/recovery-matrix-evaluate.mjs rpo      <rpo-evidence.json | ->
//
// WHAT IS AUTHORITY (review K3): a restore or RPO verdict counts only when its
// input is the verifier's stdout PIPED in ("-") — `restore-watermark.py
// verify-restore | ... restore -` or `pitr-restore-proof.py prove | ... rpo -`.
// A verdict computed on a saved file is a replay for the operator's eyes: the
// binding is not a signature, so nothing proves the file is what the verifier
// printed.
//   node mcp-server/bin/recovery-matrix-evaluate.mjs matrix   <matrix-evidence.json | ->
//   node mcp-server/bin/recovery-matrix-evaluate.mjs outbound <outbound-request.json | ->
//   node mcp-server/bin/recovery-matrix-evaluate.mjs degraded <dependency> [<dependency> ...]
//   node mcp-server/bin/recovery-matrix-evaluate.mjs policy
//
// THE CLOCK IS THIS PROCESS'S, NEVER THE EVIDENCE'S. rpo, matrix and outbound
// are judged at Date.now(); an evidence file that carries its own observed_at
// is refused (exit 2), so a stale proof cannot be replayed as a fresh one.
//
// `rpo` evaluates one RPO evidence block — in production the block
// `tools/pitr-restore-proof.py prove` reads itself from production, the
// branch and the provider, binds, and pipes in on stdin ("-"). `matrix` supplies the repository's
// sealed business calendar (ops/config/business-calendar.us-federal.json) when
// the evidence does not carry one; the evaluator refuses any calendar but the
// pinned one.
//
// Read-only: it reads local files or stdin and prints the verdict. Exit 0 when
// the verdict is pass / reconciled, 1 when it is not, 2 when the evidence cannot
// be read at all (a contract violation, never a policy answer).

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  evaluateRestoreExercise,
  evaluateRecoveryMatrix,
  evaluateOutboundQueueRelease,
  v5DegradedModeProjection,
  v5RecoveryMatrixPolicyDigest,
} from "../src/recovery-matrix.v5.js";

const CALENDAR_PATH = fileURLToPath(new URL("../../ops/config/business-calendar.us-federal.json", import.meta.url));
const [cmd, ...rest] = process.argv.slice(2);
const read = path => JSON.parse(readFileSync(path === "-" ? 0 : path, "utf8"));

// The clock is read AFTER the evidence: with `verify | evaluate`, the evaluator
// starts before its producer finishes, and a clock taken at start-up would sit
// before the readbacks it is judging.
const clockNow = () => ({ now_ms: Date.now() });

try {
  let result;
  let passed;
  if (cmd === "restore") {
    const receipt = read(rest[0]);
    result = evaluateRestoreExercise(receipt, clockNow());
  } else if (cmd === "rpo") {
    const block = read(rest[0]);
    const matrix = evaluateRecoveryMatrix({ cells: { record_layer_rpo: block } }, clockNow());
    result = { observed_at: matrix.observed_at, policy_digest: matrix.policy_digest, record_layer_rpo: matrix.cells.record_layer_rpo };
    passed = result.record_layer_rpo.state === "pass";
  } else if (cmd === "matrix") {
    const evidence = read(rest[0]);
    if (evidence !== null && typeof evidence === "object" && evidence.business_calendar === undefined) {
      evidence.business_calendar = read(CALENDAR_PATH);
    }
    result = evaluateRecoveryMatrix(evidence, clockNow());
  } else if (cmd === "outbound") {
    const request = read(rest[0]);
    result = evaluateOutboundQueueRelease(request, clockNow());
  } else if (cmd === "degraded") {
    result = v5DegradedModeProjection(rest);
  } else if (cmd === "policy") {
    result = { policy_digest: v5RecoveryMatrixPolicyDigest() };
  } else {
    process.stderr.write("usage: recovery-matrix-evaluate.mjs restore|rpo|matrix|outbound <file.json|-> | degraded <dep>... | policy\n");
    process.exit(2);
  }
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  if (passed === undefined) passed = !["fail", "hold"].includes(result.decision);
  process.exit(passed ? 0 : 1);
} catch (error) {
  process.stderr.write(`recovery-matrix-evaluate: ${error.code ?? "error"}: ${error.message}\n`);
  process.exit(2);
}
