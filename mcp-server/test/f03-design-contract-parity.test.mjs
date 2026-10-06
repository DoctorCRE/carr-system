// V5-F03 cross-language design-contract parity regression.
//
// The engineering-slice-plan.v2 design contract is enforced twice: once by the
// server validator in mcp-server/src/engineering-runtime.js (requirePlan) and
// once by the portable validator in tools/room-bridge/engineering_passport.py
// (validate_engineering_slice_plan).  Both files say in prose that the two
// accept and refuse exactly the same v2 input.  Nothing checked it: the two
// existing suites keep hand-maintained parallel case tables that are "kept
// identical on purpose", which drifts the moment one side is edited alone.
//
// This test replaces that promise with one shared, versioned, bounded corpus:
// test/fixtures/f03-design-contract-parity.v1.json.  Every vector is a
// declarative patch over a named base plan, so neither language reconstructs a
// fixture and neither restates a validation rule.  The corpus records the
// expected verdict, so the server half is checked even where no Python runtime
// exists, and the two halves are then compared against each other.
//
// SCOPE OF THE PARITY CLAIM.  Equivalence is claimed at plan registration,
// which is the one seam both validators expose publicly with the same input
// shape.  Three things are deliberately NOT claimed:
//
//   1. Legacy engineering-slice-plan.v1 duplicate ordinals, dependency cycles
//      and whitespace-padded identifiers.  The portable validator has always
//      refused all three; the server validator has always accepted them,
//      because requirePlan re-runs against the STORED append-only plan row on
//      every read path and refusing them now would strand an already registered
//      passport instead of repairing it.  Those divergences are documented in
//      engineering-runtime.js, and the vectors below assert BOTH halves of each
//      exactly rather than quietly excluding the shapes or "fixing" the older
//      acceptance.  Every one of them is refused by BOTH validators under
//      engineering-slice-plan.v2, which is what the successor version is for.
//   2. The contract-versus-issued-binding refusal.  On the server it lives
//      inside admitEngineeringSlice, which needs a database connection; in the
//      portable validator it lives inside build_engineering_slice_packet, which
//      needs a full execution-envelope fixture of a different shape.  No public
//      export on either side takes the same two arguments, so this corpus
//      records what the plan validators actually do with those contradictions
//      (accept them; the refusal happens later) instead of inventing a parity
//      claim the current exports cannot support.
//   3. Receipts, packets, envelopes, passports and closure projection.  This
//      file touches none of them.
//
// This test performs no database, network, filesystem-write or record-layer
// work.  It reads one fixture and runs one local Python interpreter with the
// program below on stdin and no shell.

import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  generateF03SqlCorpus,
  PLAN_OUTPUT_PATH,
  SLICE_OUTPUT_PATH,
} from "./f03-sql-corpus-generator.mjs";
import {
  canonicalDigest,
  classifyDesignDepth,
  designDepthInputs,
  requirePlan,
  ENGINEERING_DESIGN_CONTRACT_VERSION,
  ENGINEERING_DESIGN_DEPTH_PREDICATE_VERSIONS,
  ENGINEERING_SERVER_EXECUTION_BINDING,
  ENGINEERING_SLICE_PLAN_VERSIONS,
} from "../src/engineering-runtime.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");
const ROOM_BRIDGE = path.join(REPO_ROOT, "tools", "room-bridge");
const CORPUS_PATH = path.join(HERE, "fixtures", "f03-design-contract-parity.v1.json");
const DIGEST = /^sha256:[0-9a-f]{64}$/;

const corpus = JSON.parse(readFileSync(CORPUS_PATH, "utf8"));

class EngineeringToolError extends Error {
  constructor(payload) {
    super(payload?.error || "engineering tool error");
    Object.assign(this, payload);
  }
}

// --- the shared corpus applier ------------------------------------------------
//
// Four pure data operations.  They move JSON around and nothing else: no
// validation rule is restated here, so a rule that exists in only one validator
// cannot be accidentally satisfied by the harness.  The Python program further
// down implements the identical four operations against the identical corpus.

function containerAt(root, segments) {
  let node = root;
  for (const segment of segments) node = node[segment];
  return node;
}

function applyOp(root, op) {
  const segments = op.path;
  if (op.op === "reorder") {
    if (!segments.length) return Object.fromEntries(Object.entries(root).reverse());
    const holder = containerAt(root, segments.slice(0, -1));
    const key = segments[segments.length - 1];
    holder[key] = Object.fromEntries(Object.entries(holder[key]).reverse());
    return root;
  }
  if (op.op === "append") {
    containerAt(root, segments).push(structuredClone(op.value));
    return root;
  }
  const holder = containerAt(root, segments.slice(0, -1));
  const key = segments[segments.length - 1];
  if (op.op === "delete") delete holder[key];
  else if (op.op === "set") holder[key] = structuredClone(op.value);
  else throw new Error(`unknown corpus op: ${op.op}`);
  return root;
}

function withoutDigest(plan) {
  return Object.fromEntries(Object.entries(plan).filter(([key]) => key !== "plan_digest"));
}

function materializePlan(vector) {
  let plan = structuredClone(corpus.bases[vector.base]);
  // Normalize the base first, so "as_is" means a digest that was genuinely
  // valid for the UNPATCHED plan and is stale only because the ops changed it.
  plan.plan_digest = canonicalDigest(withoutDigest(plan));
  for (const op of vector.ops || []) plan = applyOp(plan, op);
  const seal = vector.seal || "reseal";
  if (seal === "reseal") plan.plan_digest = canonicalDigest(withoutDigest(plan));
  else if (seal === "literal") plan.plan_digest = vector.plan_digest;
  else if (seal !== "as_is") throw new Error(`unknown seal mode: ${seal}`);
  return plan;
}

function materializeSlice(vector) {
  let row = structuredClone(corpus.bases[vector.base].slices[vector.slice_index]);
  for (const op of vector.ops || []) row = applyOp(row, op);
  return row;
}

function serverPlanVerdict(vector) {
  try {
    requirePlan(materializePlan(vector), EngineeringToolError);
    return { verdict: "accepted", error_type: null, message: null };
  } catch (thrown) {
    return {
      verdict: "rejected",
      error_type: thrown?.constructor?.name ?? null,
      message: thrown?.error ?? thrown?.message ?? null,
    };
  }
}

function serverDepthVerdict(vector) {
  const row = materializeSlice(vector);
  try {
    const depth = Object.hasOwn(vector, "contract_version")
      ? classifyDesignDepth(row, EngineeringToolError, vector.contract_version)
      : classifyDesignDepth(row, EngineeringToolError);
    return { verdict: depth, error_type: null };
  } catch (thrown) {
    return { verdict: "error", error_type: thrown?.constructor?.name ?? null };
  }
}

const planVectors = [...corpus.vectors, ...corpus.divergence_vectors];

// --- the portable validator, driven over the identical corpus -----------------

const PYTHON_RUNNER = `
import json
import sys

room_bridge, corpus_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, room_bridge)

import engineering_passport as ep
import execution_contract as contract


def deep_copy(value):
    return json.loads(json.dumps(value))


def container_at(root, segments):
    node = root
    for segment in segments:
        node = node[segment]
    return node


def apply_op(root, op):
    segments = op["path"]
    kind = op["op"]
    if kind == "reorder":
        if not segments:
            return dict(reversed(list(root.items())))
        holder = container_at(root, segments[:-1])
        key = segments[-1]
        holder[key] = dict(reversed(list(holder[key].items())))
        return root
    if kind == "append":
        container_at(root, segments).append(deep_copy(op["value"]))
        return root
    holder = container_at(root, segments[:-1])
    key = segments[-1]
    if kind == "delete":
        del holder[key]
    elif kind == "set":
        holder[key] = deep_copy(op["value"])
    else:
        raise SystemExit("unknown corpus op: " + kind)
    return root


def without_digest(plan):
    return {key: value for key, value in plan.items() if key != "plan_digest"}


def materialize_plan(corpus, vector):
    plan = deep_copy(corpus["bases"][vector["base"]])
    plan["plan_digest"] = contract.canonical_digest(without_digest(plan))
    for op in vector.get("ops", []):
        plan = apply_op(plan, op)
    seal = vector.get("seal", "reseal")
    if seal == "reseal":
        plan["plan_digest"] = contract.canonical_digest(without_digest(plan))
    elif seal == "literal":
        plan["plan_digest"] = vector["plan_digest"]
    elif seal != "as_is":
        raise SystemExit("unknown seal mode: " + seal)
    return plan


def materialize_slice(corpus, vector):
    row = deep_copy(corpus["bases"][vector["base"]]["slices"][vector["slice_index"]])
    for op in vector.get("ops", []):
        row = apply_op(row, op)
    return row


with open(corpus_path, encoding="utf-8") as handle:
    corpus = json.load(handle)

out = {"plans": {}, "depths": {}, "depth_inputs": {}, "digests": {}, "constants": {}}

for vector in corpus["vectors"] + corpus["divergence_vectors"]:
    try:
        ep.validate_engineering_slice_plan(materialize_plan(corpus, vector))
        out["plans"][vector["id"]] = {"verdict": "accepted", "error_type": None, "message": None}
    except Exception as exc:
        out["plans"][vector["id"]] = {
            "verdict": "rejected", "error_type": type(exc).__name__, "message": str(exc)[:240],
        }

for vector in corpus["depth_vectors"]:
    try:
        row = materialize_slice(corpus, vector)
        if "contract_version" in vector:
            depth = ep.classify_design_depth(row, vector["contract_version"])
        else:
            depth = ep.classify_design_depth(row)
        out["depths"][vector["id"]] = {"verdict": depth, "error_type": None}
    except Exception as exc:
        out["depths"][vector["id"]] = {"verdict": "error", "error_type": type(exc).__name__}

for vector in corpus["depth_input_vectors"]:
    out["depth_inputs"][vector["id"]] = ep.design_depth_inputs(materialize_slice(corpus, vector))

for vector in corpus["digest_vectors"]:
    out["digests"][vector["id"]] = [contract.canonical_digest(item) for item in vector["inputs"]]

out["constants"] = {
    "slice_plan_versions": list(ep.ENGINEERING_SLICE_PLAN_VERSIONS),
    "design_contract_version": ep.DESIGN_CONTRACT_VERSION,
    "design_depth_predicate_versions": list(ep.DESIGN_DEPTH_PREDICATE_VERSIONS),
}

sys.stdout.write(json.dumps(out, sort_keys=True))
`;

function resolvePython() {
  const candidates = [
    process.env.F03_PARITY_PYTHON,
    process.env.PYTHON,
    path.join(REPO_ROOT, ".venv", "bin", "python3"),
    path.join(REPO_ROOT, ".venv", "bin", "python"),
    "python3",
    "python",
  ];
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (candidate.includes(path.sep) && !existsSync(candidate)) continue;
    const probe = spawnSync(candidate, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"],
      { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  return null;
}

let portableRun = null;

function portableResults() {
  if (portableRun) return portableRun;
  const python = resolvePython();
  if (!python) {
    portableRun = { available: false, reason: "no local Python 3.9+ runtime was found on PATH or in .venv" };
    return portableRun;
  }
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
  delete env.PYTHONPATH;
  // argv, not a shell: the program arrives on stdin and the two paths are
  // ordinary arguments, so nothing here is interpolated into a command line.
  const run = spawnSync(python, ["-", ROOM_BRIDGE, CORPUS_PATH], {
    cwd: REPO_ROOT, input: PYTHON_RUNNER, encoding: "utf8", env, maxBuffer: 64 * 1024 * 1024,
  });
  if (run.error) throw run.error;
  if (run.status !== 0)
    throw new Error(`the portable validator run exited ${run.status}:\n${run.stderr}`);
  portableRun = { available: true, results: JSON.parse(run.stdout) };
  return portableRun;
}

function portableOrSkip(t) {
  const run = portableResults();
  if (run.available) return run.results;
  if (process.env.F03_PARITY_REQUIRE_PYTHON === "1")
    assert.fail(`cross-language parity was required but could not run: ${run.reason}`);
  t.skip(`cross-language parity skipped: ${run.reason}. Set F03_PARITY_REQUIRE_PYTHON=1 to make this a failure.`);
  return null;
}

// --- corpus well-formedness ---------------------------------------------------

test("the versioned parity corpus is well formed, described and self-consistent", () => {
  assert.equal(corpus.corpus_version, "f03-design-contract-parity.v1");
  const groups = [corpus.vectors, corpus.divergence_vectors, corpus.depth_vectors,
    corpus.depth_input_vectors, corpus.digest_vectors];
  const ids = new Set();
  for (const group of groups) {
    assert.ok(Array.isArray(group) && group.length, "every corpus group must carry vectors");
    for (const vector of group) {
      assert.ok(typeof vector.id === "string" && vector.id.length, "every vector needs an id");
      assert.ok(!ids.has(vector.id), `duplicate corpus vector id: ${vector.id}`);
      ids.add(vector.id);
      assert.ok(typeof vector.describe === "string" && vector.describe.trim().length > 20,
        `vector ${vector.id} must describe what it pins in prose`);
      if (vector.base) assert.ok(corpus.bases[vector.base], `vector ${vector.id} names an unknown base`);
    }
  }
  for (const vector of planVectors)
    assert.ok(["reseal", "as_is", "literal"].includes(vector.seal || "reseal"),
      `vector ${vector.id} uses an unknown seal mode`);
  for (const vector of corpus.vectors)
    assert.ok(["accepted", "rejected"].includes(vector.expect), `vector ${vector.id} has no verdict`);
  for (const vector of corpus.divergence_vectors)
    for (const key of ["expect_javascript", "expect_python"])
      assert.ok(["accepted", "rejected"].includes(vector[key]),
        `divergence vector ${vector.id} must state ${key} explicitly`);
  for (const vector of corpus.depth_vectors)
    assert.ok(["short", "full", "error"].includes(vector.expect), `depth vector ${vector.id} has no verdict`);

  // Every sha256 literal in the corpus is either a real digest or one of the
  // deliberately malformed ones a vector uses on purpose.
  const allowed = new Set(corpus.intentionally_invalid_digests);
  const walk = value => {
    if (Array.isArray(value)) return value.forEach(walk);
    if (value && typeof value === "object") return Object.values(value).forEach(walk);
    if (typeof value === "string" && value.startsWith("sha256:"))
      assert.ok(DIGEST.test(value) || allowed.has(value), `malformed corpus digest literal: ${value}`);
  };
  walk(corpus);
});

test("the corpus covers every design contract facet with both a missing-field and a boundary vector", () => {
  const facets = Object.keys(corpus.bases["v2-short"].slices[0].design_contract).sort();
  assert.equal(facets.length, 16, "the Q046.D1 slice contract is a closed sixteen-field set");
  const missing = corpus.vectors
    .filter(vector => vector.id.startsWith("facet.missing."))
    .map(vector => vector.ops[0].path[vector.ops[0].path.length - 1])
    .sort();
  assert.deepEqual(missing, facets, "every facet needs a missing-field vector, and only real facets may have one");
  for (const facet of facets)
    assert.ok(corpus.vectors.some(vector => vector.id.startsWith(`facet.${facet}.`)),
      `facet ${facet} needs at least one boundary vector beside its missing-field vector`);
  // The two named baselines really are the two design depths, so "positives
  // FULL and SHORT" is a fact about the corpus and not just about its labels.
  assert.equal(classifyDesignDepth(corpus.bases["v2-short"].slices[0], EngineeringToolError), "short");
  assert.equal(classifyDesignDepth(corpus.bases["v2-full"].slices[0], EngineeringToolError), "full");
  assert.equal(corpus.bases["v1-legacy"].schema_version, "engineering-slice-plan.v1");
});

// --- the server validator against the recorded corpus -------------------------

test("the server validator matches the recorded verdict for every corpus vector", () => {
  const disagreements = [];
  for (const vector of corpus.vectors) {
    const actual = serverPlanVerdict(vector);
    if (actual.verdict !== vector.expect)
      disagreements.push(`${vector.id}: expected ${vector.expect}, got ${actual.verdict} (${actual.message})`);
    if (actual.verdict === "rejected")
      assert.equal(actual.error_type, "EngineeringToolError",
        `${vector.id} must be refused by the contract, not crash: ${actual.message}`);
  }
  assert.deepEqual(disagreements, [], "the server validator drifted from the recorded corpus");
});

test("the server design-depth classifier matches every recorded depth vector", () => {
  const disagreements = [];
  for (const vector of corpus.depth_vectors) {
    const actual = serverDepthVerdict(vector);
    if (actual.verdict !== vector.expect)
      disagreements.push(`${vector.id}: expected ${vector.expect}, got ${actual.verdict}`);
    if (actual.verdict === "error")
      assert.equal(actual.error_type, "EngineeringToolError",
        `${vector.id} must be refused by the classifier, not crash`);
  }
  assert.deepEqual(disagreements, [], "the server classifier drifted from the recorded corpus");
});

test("the server canonical digest matches every recorded digest vector", () => {
  for (const vector of corpus.digest_vectors) {
    const digests = vector.inputs.map(input => canonicalDigest(input));
    for (const digest of digests) assert.match(digest, DIGEST, vector.id);
    if (vector.expect_digest) assert.equal(digests[0], vector.expect_digest, vector.id);
    if (vector.inputs.length === 2)
      assert.equal(digests[0] === digests[1], vector.expect_equal, vector.id);
  }
});

test("the corpus routing and authority match the execution binding this server actually issues", () => {
  // Not a parity assertion: the portable validator has no equivalent constant.
  // It stops the corpus drifting away from the binding admission compares a
  // sealed contract against, which is what would make these vectors meaningless.
  const contract = corpus.bases["v2-short"].slices[0].design_contract;
  assert.equal(contract.routing.adapter_ref, ENGINEERING_SERVER_EXECUTION_BINDING.adapter_ref);
  assert.equal(contract.authority.environment, ENGINEERING_SERVER_EXECUTION_BINDING.environment);
  assert.deepEqual(corpus.constants.server_execution_binding, {
    adapter_ref: ENGINEERING_SERVER_EXECUTION_BINDING.adapter_ref,
    environment: ENGINEERING_SERVER_EXECUTION_BINDING.environment,
  });
  assert.deepEqual([...ENGINEERING_SLICE_PLAN_VERSIONS], corpus.constants.slice_plan_versions);
  assert.equal(ENGINEERING_DESIGN_CONTRACT_VERSION, corpus.constants.design_contract_version);
  assert.deepEqual([...ENGINEERING_DESIGN_DEPTH_PREDICATE_VERSIONS],
    corpus.constants.design_depth_predicate_versions);
});

// --- the portable validator, and the two compared -----------------------------

test("the portable validator matches the recorded verdict for every corpus vector", t => {
  const results = portableOrSkip(t);
  if (!results) return;
  const disagreements = [];
  for (const vector of corpus.vectors) {
    const actual = results.plans[vector.id];
    assert.ok(actual, `the portable validator returned nothing for ${vector.id}`);
    if (actual.verdict !== vector.expect)
      disagreements.push(`${vector.id}: expected ${vector.expect}, got ${actual.verdict} (${actual.message})`);
    if (actual.verdict === "rejected")
      assert.equal(actual.error_type, "EngineeringContractError",
        `${vector.id} must be refused by the contract, not crash: ${actual.message}`);
  }
  assert.deepEqual(disagreements, [], "the portable validator drifted from the recorded corpus");
});

test("both validators accept and refuse exactly the same v2 design contracts and whole plans", t => {
  const results = portableOrSkip(t);
  if (!results) return;
  const disagreements = [];
  for (const vector of corpus.vectors) {
    const server = serverPlanVerdict(vector).verdict;
    const portable = results.plans[vector.id].verdict;
    if (server !== portable)
      disagreements.push(`${vector.id}: server ${server}, portable ${portable} -- ${vector.describe}`);
  }
  assert.deepEqual(disagreements, [],
    "the two validators disagree about a plan neither documents as a divergence");
  assert.ok(corpus.vectors.length >= 100,
    "the equivalence claim needs a corpus wide enough to be worth making");
});

test("both design-depth classifiers agree on every accepted depth and every refusal", t => {
  const results = portableOrSkip(t);
  if (!results) return;
  const disagreements = [];
  for (const vector of corpus.depth_vectors) {
    const server = serverDepthVerdict(vector).verdict;
    const portable = results.depths[vector.id].verdict;
    if (server !== portable)
      disagreements.push(`${vector.id}: server ${server}, portable ${portable}`);
  }
  assert.deepEqual(disagreements, [], "the frozen SHORT predicate is not the same in both validators");
});

test("both validators derive identical bound classifier inputs, and neither reads the planned check count", t => {
  const results = portableOrSkip(t);
  if (!results) return;
  for (const vector of corpus.depth_input_vectors) {
    const server = JSON.parse(JSON.stringify(designDepthInputs(materializeSlice(vector), EngineeringToolError)));
    assert.deepEqual(server, results.depth_inputs[vector.id], vector.id);
    assert.ok(!JSON.stringify(server).includes("planned_check"),
      `${vector.id}: extra verification must never be a classifier input`);
  }
  const baseline = results.depth_inputs["inputs.short-baseline"];
  assert.deepEqual(results.depth_inputs["inputs.extra-planned-checks-change-nothing"], baseline,
    "adding planned checks changed the bound classifier inputs");
});

test("both canonical digest implementations produce byte-identical digests", t => {
  const results = portableOrSkip(t);
  if (!results) return;
  for (const vector of corpus.digest_vectors) {
    const server = vector.inputs.map(input => canonicalDigest(input));
    assert.deepEqual(server, results.digests[vector.id], vector.id);
  }
});

test("both validators report the same slice plan and design contract versions", t => {
  const results = portableOrSkip(t);
  if (!results) return;
  assert.deepEqual(results.constants.slice_plan_versions, corpus.constants.slice_plan_versions);
  assert.equal(results.constants.design_contract_version, corpus.constants.design_contract_version);
  assert.deepEqual(results.constants.design_depth_predicate_versions,
    corpus.constants.design_depth_predicate_versions);
});

// The documented legacy v1 exclusions, named one by one rather than counted.
// A shape may only leave the equivalence claim by appearing here with the prose
// that justifies it, and a shape that stops diverging has to leave this list
// rather than sit in the corpus unasserted.
const DOCUMENTED_V1_DIVERGENCES = [
  "divergence.v1.duplicate-ordinals",
  "divergence.v1.padded-accepted-plan-revision-id",
  "divergence.v1.padded-baseline-evidence-ref",
  "divergence.v1.padded-declared-resource-ref",
  "divergence.v1.padded-forbidden-change-ref",
  "divergence.v1.padded-planned-check-ref",
  "divergence.v1.padded-slice-ref",
  "divergence.v1.padded-work-request-id",
  "divergence.v1.self-dependency",
  "divergence.v1.two-slice-cycle",
];

// Each documented v1 divergence has a v2 counterpart that BOTH validators must
// refuse, so the divergence really is confined to the legacy version.
const V2_COUNTERPARTS_REFUSED_BY_BOTH = [
  "graph.v2.duplicate-ordinal",
  "graph.v2.self-dependency",
  "graph.v2.two-slice-cycle",
  "identity.v2.accepted-plan-revision-id-padded",
  "identity.v2.baseline-evidence-ref-padded",
  "identity.v2.declared-resource-ref-padded",
  "identity.v2.forbidden-change-ref-padded",
  "identity.v2.planned-check-ref-padded",
  "identity.v2.slice-ref-leading-and-trailing-space",
  "identity.v2.work-request-id-padded",
];

test("the documented engineering-slice-plan.v1 divergence is asserted exactly, never silently equalized", t => {
  const results = portableOrSkip(t);
  if (!results) return;
  assert.deepEqual(corpus.divergence_vectors.map(vector => vector.id).sort(), DOCUMENTED_V1_DIVERGENCES,
    "only the documented legacy v1 shapes may be excluded from the equivalence claim");
  for (const vector of corpus.divergence_vectors) {
    assert.equal(corpus.bases[vector.base].schema_version, "engineering-slice-plan.v1",
      `${vector.id}: only legacy v1 carries a documented divergence`);
    assert.equal(serverPlanVerdict(vector).verdict, vector.expect_javascript,
      `${vector.id}: the server validator's legacy acceptance must not change -- ${vector.describe}`);
    assert.equal(results.plans[vector.id].verdict, vector.expect_python,
      `${vector.id}: the portable validator's long-standing refusal must not change`);
    // VERDICT IS NOT ENOUGH ON THIS SIDE. The Python runner records "rejected"
    // for any exception at all, so a harness fault -- a KeyError from a path an
    // op no longer reaches, say -- would read as the documented contract
    // refusal and keep this test green while the divergence went unasserted.
    // Same guard the corpus-vector rejections above already carry.
    if (vector.expect_python === "rejected")
      assert.equal(results.plans[vector.id].error_type, "EngineeringContractError",
        `${vector.id}: the portable refusal must come from the contract, not a crash in the corpus applier: ${results.plans[vector.id].message}`);
    assert.notEqual(vector.expect_javascript, vector.expect_python,
      `${vector.id}: a vector listed as a divergence must actually diverge`);
  }
  // The same shapes under the successor version are refused by BOTH, so the
  // divergence is confined to legacy v1 exactly as documented.
  for (const id of V2_COUNTERPARTS_REFUSED_BY_BOTH) {
    const vector = corpus.vectors.find(row => row.id === id);
    assert.ok(vector, `the corpus is missing the v2 counterpart ${id}`);
    assert.equal(corpus.bases[vector.base].schema_version, "engineering-slice-plan.v2", id);
    assert.equal(serverPlanVerdict(vector).verdict, "rejected", id);
    assert.equal(results.plans[id].verdict, "rejected", id);
  }
});

test("engineering-slice-plan.v2 admits identifiers exactly as written, and names the refusal it uses", () => {
  // Server-side only: the shared corpus already pins the verdict both
  // validators reach.  This pins WHICH refusal the server reaches, so a later
  // edit cannot quietly turn the refusal into a silent trim-and-accept and
  // still leave the corpus green.  Every code below is one the identifier
  // contract already had; no new error class is introduced.
  const expected = {
    "identity.v2.slice-ref-leading-and-trailing-space": "engineering_identifier_invalid",
    "identity.v2.slice-ref-leading-tab": "engineering_identifier_invalid",
    "identity.v2.slice-ref-trailing-newline": "engineering_identifier_invalid",
    "identity.v2.slice-ref-trailing-carriage-return": "engineering_identifier_invalid",
    "identity.v2.slice-ref-mixed-leading-and-trailing-controls": "engineering_identifier_invalid",
    "identity.v2.slice-ref-repeated-trailing-space": "engineering_identifier_invalid",
    "identity.v2.slice-ref-whitespace-only": "engineering_field_required",
    "identity.v2.work-request-id-padded": "engineering_identifier_invalid",
    "identity.v2.accepted-plan-revision-id-padded": "engineering_identifier_invalid",
    "identity.v2.declared-resource-ref-padded": "engineering_identifier_invalid",
    "identity.v2.declared-component-ref-padded": "engineering_identifier_invalid",
    "identity.v2.declared-plan-step-ref-padded": "engineering_identifier_invalid",
    "identity.v2.forbidden-change-ref-padded": "engineering_identifier_invalid",
    "identity.v2.planned-check-ref-padded": "engineering_identifier_invalid",
    "identity.v2.baseline-evidence-ref-padded": "engineering_identifier_invalid",
    // A padded dependency reference names no slice, so the existing
    // unknown-dependency refusal reaches it first.  Same verdict, earlier route.
    "identity.v2.dependency-ref-padded": "engineering_slice_dependency_unknown",
  };
  for (const [id, code] of Object.entries(expected)) {
    const vector = corpus.vectors.find(row => row.id === id);
    assert.ok(vector, `the corpus is missing ${id}`);
    assert.equal(vector.expect, "rejected", id);
    assert.deepEqual(serverPlanVerdict(vector), { verdict: "rejected", error_type: "EngineeringToolError", message: code }, id);
  }
  // The two positives keep the boundary honest: an exact identifier using the
  // whole accepted character set is still accepted, and prose keeps its exact
  // previous whitespace behavior, so nothing here reaches free text.
  for (const id of ["identity.v2.exact-identifier-accepted", "identity.v2.padded-objective-is-still-prose"]) {
    const vector = corpus.vectors.find(row => row.id === id);
    assert.ok(vector, `the corpus is missing ${id}`);
    assert.equal(serverPlanVerdict(vector).verdict, "accepted", id);
  }
  // The refusal never rewrites the caller's plan: a padded identifier stays
  // padded, so no reader can observe a trimmed identity that was never
  // registered and never sealed into plan_digest.
  const padded = materializePlan(corpus.vectors.find(row => row.id === "identity.v2.slice-ref-leading-and-trailing-space"));
  const before = JSON.stringify(padded);
  assert.throws(() => requirePlan(padded, EngineeringToolError), EngineeringToolError);
  assert.equal(JSON.stringify(padded), before, "a refused plan must come back exactly as it was handed in");
  assert.equal(padded.slices[0].slice_ref, " slice:short ");
});

// --- the SQL leg, bound to the same corpus ------------------------------------
//
// The design contract is stated three times -- JS, Python and SQL -- and until
// now executed parity existed between two of them.  The SQL leg's two fixtures
// were hand-written vectors that consumed none of this corpus, so on the day
// someone ran them against a database they would have proved the SQL against a
// DIFFERENT set of cases than the two validators it has to agree with, and a
// vector added here would never have reached it.
//
// mcp-server/test/f03-sql-corpus-generator.mjs emits the SQL leg's cases FROM
// this corpus instead, and the two hand-written fixtures include what it emits.
// These tests are what make the generated files trustworthy without a database:
// a generated fixture anyone can hand-edit is the same defect one level down,
// so the bytes on disk are compared against a fresh generation, and the wiring
// that carries them into the two fixtures is asserted rather than assumed.
//
// None of this runs SQL.  It reads files and compares strings.

test("the generated SQL corpus parts are exactly what this corpus generates", () => {
  const generated = generateF03SqlCorpus();
  const targets = Object.keys(generated);
  assert.equal(targets.length, 2, "one generated part per SQL seam");
  for (const target of targets) {
    const onDisk = readFileSync(target, "utf8");
    assert.equal(onDisk, generated[target],
      `${path.basename(target)} is not what the corpus generates -- regenerate it with `
      + "`node mcp-server/test/f03-sql-corpus-generator.mjs --write` rather than editing it");
  }
});

test("each generated part records the corpus digest it was generated from", () => {
  const corpusDigest = createHash("sha256").update(readFileSync(CORPUS_PATH)).digest("hex");
  for (const target of [PLAN_OUTPUT_PATH, SLICE_OUTPUT_PATH]) {
    const text = readFileSync(target, "utf8");
    assert.ok(text.includes(`-- corpus_sha256:  ${corpusDigest}`),
      `${path.basename(target)} does not record this corpus's sha256`);
    assert.ok(text.includes(`-- corpus_version: ${corpus.corpus_version}`),
      `${path.basename(target)} does not record this corpus's version`);
    assert.ok(text.includes("GENERATED FILE -- DO NOT EDIT"),
      `${path.basename(target)} must say it is generated`);
  }
});

test("every shared corpus vector reaches the SQL leg, and the excluded ones are named", () => {
  const planPart = readFileSync(PLAN_OUTPUT_PATH, "utf8");
  for (const vector of corpus.vectors)
    assert.ok(planPart.includes(`('${vector.id}', '${vector.expect}', `),
      `${vector.id} is not carried into the whole-plan SQL part with its corpus verdict`);
  // Nothing may be quietly missing from the per-slice part either: every v2
  // vector is either asserted there or listed with a reason.
  const slicePart = readFileSync(SLICE_OUTPUT_PATH, "utf8");
  for (const vector of corpus.vectors) {
    const asserted = slicePart.includes(`  ('${vector.id}', `);
    const excluded = slicePart.includes(`('${vector.id}', 'engineering-slice-plan.v1 base`)
      || slicePart.includes(`('${vector.id}', 'a stale or literal`)
      || slicePart.includes(`('${vector.id}', 'multi-slice base`)
      || slicePart.includes(`('${vector.id}', 'no ops`)
      || slicePart.includes(`('${vector.id}', 'an op lands outside`)
      || slicePart.includes(`('${vector.id}', 'seam authority is a plan-level rule`)
      || slicePart.includes(`('${vector.id}', 'ops span two slices`);
    assert.ok(asserted || excluded,
      `${vector.id} is neither asserted nor listed as excluded in the per-slice SQL part`);
  }
});

test("both hand-written SQL fixtures actually include their generated part", () => {
  const wiring = [
    ["f03-plan-ownership-validator-postgres.sql", "f03-design-contract-corpus-plan-postgres.generated.sql"],
    ["f03-receipt-validator-postgres.sql", "f03-design-contract-corpus-slice-postgres.generated.sql"],
  ];
  for (const [fixture, part] of wiring) {
    const text = readFileSync(path.join(HERE, fixture), "utf8");
    assert.ok(text.includes(`\\ir ${part}`),
      `${fixture} no longer includes ${part}, so the shared corpus never reaches that seam`);
  }
});
