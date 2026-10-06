// Behavioral tests for the DoctorCRE v5 portfolio hierarchy substrate.
//
// The graph under test is the AUTHENTICATED one, not 21 invented nodes: the
// fixture was generated from the r7 constitution's dependency_dag (file digest
// sha256:403d8c7a…) and the reviewed slice catalog's portfolio_milestones_21
// (artifact digest sha256:79e46712…), with the four child-program names from the
// accepted design identity in the WR-000062 bootstrap packet. The fixture is
// TEST DATA. It is never doctrine and never an acceptance: the record layer
// remains the only authority for what the portfolio is, and no test here
// asserts that any portfolio has been accepted.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  CHILD_PROGRAM_REFS,
  MASTER_NODE_COUNT,
  CHILD_PROGRAM_COUNT,
  PORTFOLIO_REVISION_SCHEMA_VERSION,
  PortfolioValidationError,
  portfolioReadback,
  portfolioRevisionCanonicalBytes,
  portfolioRevisionDigest,
  portfolioRevisionPreimage,
  validatePortfolioRevision,
  PORTFOLIO_ACCEPTED_SCHEMA_VERSION,
  portfolioAcceptedCanonicalBytes,
  portfolioAcceptedPreimage,
  portfolioChildDigest,
  validatePortfolioRevisionForPersistence,
} from "../src/work-portfolio.js";
// The same hash the module uses, so the recorded bytes can be re-hashed here
// independently of the module's own digest helpers.
import { digest } from "../src/artifact-trust.js";

const FIXTURE_PATH = fileURLToPath(new URL("./fixtures/doctorcre-portfolio-21-node.json", import.meta.url));
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8"));

/** A deep clone, so one test's mutation can never leak into another's fixture. */
function revision(mutate) {
  const clone = JSON.parse(JSON.stringify(FIXTURE.revision));
  if (mutate) mutate(clone);
  return clone;
}

function refusalCode(fn) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof PortfolioValidationError,
      `expected a PortfolioValidationError, got ${error?.name}: ${error?.message}`);
    return error.code;
  }
  return assert.fail("expected a refusal, but the call returned");
}

test("the fixture records the canonical source it was derived from", () => {
  assert.equal(FIXTURE.canonical_source.catalog_section, "5882b0cd-16f4-4896-b567-eb0fca6554f7@1");
  assert.equal(FIXTURE.canonical_source.node_identity_constitution_file_sha256,
    "sha256:403d8c7ae90c12415d2df26b13b0425003273e82f1d2f9d1cd7ecb72c03dcd64");
  assert.equal(FIXTURE.canonical_source.node_ordinal_artifact_sha256,
    "sha256:79e467125a0b5300e51f1f779b8eed2406f17cf1b085a18df96b0ad6a399fa6f");
  assert.match(FIXTURE.fixture_status, /TEST_DATA_ONLY/);
});

test("the authenticated 21-node graph validates with four children and a closed acyclic edge set", () => {
  const view = validatePortfolioRevision(revision());
  assert.equal(view.node_count, MASTER_NODE_COUNT);
  assert.equal(view.child_program_count, CHILD_PROGRAM_COUNT);
  assert.deepEqual(view.child_program_refs, CHILD_PROGRAM_REFS);
  assert.equal(view.acyclic, true);
  assert.equal(view.topological_order.length, MASTER_NODE_COUNT);
  // Prerequisites come first: the constitution node opens the order, and the
  // final reconciliation closes it.
  assert.equal(view.topological_order[0], "step:portfolio-constitution-accepted");
  assert.equal(view.topological_order.at(-1), "step:final-portfolio-outcome-reconciliation");
});

test("the topological order really respects every declared edge", () => {
  const view = validatePortfolioRevision(revision());
  const position = new Map(view.topological_order.map((ref, index) => [ref, index]));
  for (const edge of FIXTURE.revision.edges) {
    assert.ok(position.get(edge.from_node_ref) < position.get(edge.to_node_ref),
      `${edge.from_node_ref} must precede ${edge.to_node_ref}`);
  }
});

test("the digest is stable across key reordering and edge reordering", () => {
  const baseline = portfolioRevisionDigest(revision());

  const reorderedKeys = revision(draft => {
    // Rebuild every object with its keys in reverse order. Same revision, and a
    // digest that noticed would be hashing JSON layout instead of meaning.
    const reverse = value => {
      if (Array.isArray(value)) return value.map(reverse);
      if (value && typeof value === "object") {
        return Object.fromEntries(Object.keys(value).reverse().map(k => [k, reverse(value[k])]));
      }
      return value;
    };
    Object.assign(draft, reverse(draft));
  });
  assert.equal(portfolioRevisionDigest(reorderedKeys), baseline);

  const reorderedEdges = revision(draft => { draft.edges.reverse(); });
  assert.equal(portfolioRevisionDigest(reorderedEdges), baseline,
    "edges are a declared unordered set and are sorted into the preimage");

  assert.match(baseline, /^sha256:[0-9a-f]{64}$/);
});

test("node order is NOT free: reordering the ordered node set changes the digest", () => {
  const baseline = portfolioRevisionDigest(revision());
  const swapped = revision(draft => {
    const [a, b] = [draft.nodes[3], draft.nodes[4]];
    draft.nodes[3] = { ...b, ordinal: 4 };
    draft.nodes[4] = { ...a, ordinal: 5 };
  });
  assert.notEqual(portfolioRevisionDigest(swapped), baseline);
});

test("each bound source identity changes the digest when it moves", () => {
  const baseline = portfolioRevisionDigest(revision());
  const digests = new Set([baseline]);
  for (const key of ["constitution", "design", "integration", "requirements"]) {
    const moved = portfolioRevisionDigest(revision(draft => {
      draft.source_digests[key] = `sha256:${"0".repeat(64)}`;
    }));
    assert.notEqual(moved, baseline, `moving source_digests.${key} must change the revision digest`);
    assert.ok(!digests.has(moved), `source_digests.${key} must not collide with another field's change`);
    digests.add(moved);
  }
});

test("the preimage binds no timestamp, actor or acceptance fact", () => {
  const preimage = portfolioRevisionPreimage(revision());
  assert.deepEqual(Object.keys(preimage).sort(), [
    "child_program_refs", "edges", "nodes", "portfolio_ref",
    "revision_version", "schema_version", "source_digests",
  ]);
  assert.equal(preimage.schema_version, PORTFOLIO_REVISION_SCHEMA_VERSION);
  const bytes = portfolioRevisionCanonicalBytes(revision());
  for (const forbidden of ["accepted", "reviewer", "maker", "observed_at", "created_at", "timestamp"]) {
    assert.ok(!bytes.includes(`"${forbidden}"`), `the canonical bytes must not carry "${forbidden}"`);
  }
});

test("stated node metadata is bound by the digest; absent metadata is not invented", () => {
  const baseline = portfolioRevisionDigest(revision());
  const withMetadata = revision(draft => { draft.nodes[0].effect_class = "no_effect"; });
  assert.notEqual(portfolioRevisionDigest(withMetadata), baseline);
  // Absent is the only way to write "not stated": an explicit null is refused,
  // so absent and null can never mean the same thing under two digests.
  assert.equal(refusalCode(() => portfolioRevisionDigest(revision(draft => {
    draft.nodes[0].effect_class = null;
  }))), "unsupported_value");
});

test("the readback is a zero-effect projection", () => {
  const view = portfolioReadback(revision());
  assert.equal(view.accepted, false);
  assert.equal(view.effects.creates_effect, false);
  for (const [name, count] of Object.entries(view.effects)) {
    if (name === "creates_effect") continue;
    assert.equal(count, 0, `${name} must be 0 in a source-only readback`);
  }
  assert.deepEqual(view.unresolved_dependencies, []);
  assert.equal(view.revision_digest, portfolioRevisionDigest(revision()));
});

test("the readback accepts a matching expected digest and refuses a stale one", () => {
  const current = portfolioRevisionDigest(revision());
  assert.equal(portfolioReadback(revision(), { expected_digest: current }).revision_digest, current);
  assert.equal(refusalCode(() => portfolioReadback(revision(), {
    expected_digest: `sha256:${"a".repeat(64)}`,
  })), "stale_expected_digest");
});

test("distinct maker and reviewer pass; the same actor as both refuses", () => {
  assert.ok(portfolioReadback(revision(), { maker_actor: "joe", reviewer_actor: "dell" }));
  assert.equal(refusalCode(() => portfolioReadback(revision(), {
    maker_actor: "joe", reviewer_actor: "joe",
  })), "self_review");
});

test("a 22nd node and a 20-node graph both refuse on count", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes.push({
      node_ref: "step:invented-extra-node", node_kind: "milestone",
      ordinal: 22, parent_ref: draft.portfolio_ref,
    });
  }))), "node_count");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes.pop();
  }))), "node_count");
});

test("a duplicated node identity refuses even when the count still reads 21", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[5].node_ref = draft.nodes[4].node_ref;
  }))), "duplicate_node");
});

test("a fifth child, a renamed child and a reordered child set all refuse", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs.push("a-fifth-program");
  }))), "child_count");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs[1] = "assurance-fabric-v2";
  }))), "child_identity");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs.reverse();
  }))), "child_identity");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs[3] = draft.child_program_refs[0];
  }))), "duplicate_child");
});

test("a cycle refuses and names the nodes still in it", () => {
  let detail;
  try {
    validatePortfolioRevision(revision(draft => {
      // Point the opening node back at a downstream one, closing the loop.
      draft.edges.push({
        from_node_ref: "step:gate-zero-read-only-outcome",
        to_node_ref: "step:portfolio-constitution-accepted",
      });
    }));
    assert.fail("expected a cycle refusal");
  } catch (error) {
    assert.equal(error.code, "cycle");
    detail = error.detail;
  }
  assert.ok(detail.cycle.includes("step:portfolio-constitution-accepted"));
  assert.ok(detail.cycle.includes("step:gate-zero-read-only-outcome"));
});

test("a self-edge refuses as a cycle", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges.push({
      from_node_ref: "step:scheduler-active-receipt",
      to_node_ref: "step:scheduler-active-receipt",
    });
  }))), "cycle");
});

test("a dangling edge and a dangling parent both refuse", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges[0].from_node_ref = "step:not-a-node-in-this-revision";
  }))), "dangling_edge");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[2].parent_ref = "step:not-a-node-in-this-revision";
  }))), "dangling_edge");
});

test("a repeated edge refuses", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges.push({ ...draft.edges[0] });
  }))), "duplicate_edge");
});

test("an unknown field refuses at every level rather than being dropped", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.extra_top_level = "ignored?";
  }))), "unknown_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].extra_node_field = "ignored?";
  }))), "unknown_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges[0].weight = 3;
  }))), "unknown_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.source_digests.benchmark = `sha256:${"b".repeat(64)}`;
  }))), "unknown_field");
});

test("an executable-effect payload refuses anywhere in the revision", () => {
  for (const mutate of [
    draft => { draft.ops_job = "job-1"; },
    draft => { draft.nodes[0].capability = "engineering-writer"; },
    draft => { draft.nodes[0].terminal_predicate = { admission: "auto" }; },
    draft => { draft.nodes[1].recovery_ref = { nested: { scheduler: "hourly" } }; },
    draft => { draft.nodes[2].budget_identity = { accepted_by: "joe" }; },
  ]) {
    assert.equal(refusalCode(() => validatePortfolioRevision(revision(mutate))), "effect_payload_refused");
  }
});

test("values JSON cannot round-trip refuse before they reach the hash", () => {
  for (const [label, mutate] of [
    ["NaN", draft => { draft.nodes[0].budget_ceiling = Number.NaN; }],
    ["Infinity", draft => { draft.nodes[0].budget_ceiling = Number.POSITIVE_INFINITY; }],
    ["Date", draft => { draft.nodes[0].model_floor = new Date(0); }],
    ["function", draft => { draft.nodes[0].model_floor = () => "opus"; }],
    ["bigint", draft => { draft.nodes[0].budget_ceiling = 10n; }],
  ]) {
    // JSON.parse/JSON.stringify cannot carry these, so build them after cloning.
    const draft = JSON.parse(JSON.stringify(FIXTURE.revision));
    mutate(draft);
    assert.equal(refusalCode(() => validatePortfolioRevision(draft)), "unsupported_value",
      `${label} must refuse`);
  }
});

test("a wrong schema version, revision version, ordinal or node kind refuses", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.schema_version = "doctorcre-v5-portfolio-revision.v2";
  }))), "schema_version");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.revision_version = 0;
  }))), "invalid_revision_version");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[7].ordinal = 99;
  }))), "invalid_ordinal");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[7].node_kind = "workflow";
  }))), "invalid_node_kind");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    delete draft.nodes[7].parent_ref;
  }))), "missing_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.source_digests.design = "403d8c7ae90c12415d2df26b13b0425003273e82f1d2f9d1cd7ecb72c03dcd64";
  }))), "invalid_source_digest");
});

// --- review corrections: parent hierarchy is its own structure ---------------
// The dependency DAG check alone accepted a self-parent and a parent loop: both
// leave every node closure-valid while belonging to no portfolio.

test("a self-parent refuses", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].parent_ref = draft.nodes[0].node_ref;
  }))), "parent_cycle");
});

test("a two-node parent cycle refuses even though the dependency DAG is intact", () => {
  const draft = revision(d => {
    d.nodes[0].parent_ref = d.nodes[1].node_ref;
    d.nodes[1].parent_ref = d.nodes[0].node_ref;
  });
  let detail;
  try {
    validatePortfolioRevision(draft);
    assert.fail("expected a parent-hierarchy refusal");
  } catch (error) {
    assert.equal(error.code, "parent_cycle");
    detail = error.detail;
  }
  assert.deepEqual([...detail.cycle].sort(),
    [FIXTURE.revision.nodes[0].node_ref, FIXTURE.revision.nodes[1].node_ref].sort());
});

test("a three-node parent cycle refuses", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[2].parent_ref = draft.nodes[3].node_ref;
    draft.nodes[3].parent_ref = draft.nodes[4].node_ref;
    draft.nodes[4].parent_ref = draft.nodes[2].node_ref;
  }))), "parent_cycle");
});

test("a legitimate nested parent chain still validates", () => {
  // node 1 parented to node 0, node 0 still parented to the portfolio: a real
  // hierarchy, not a loop. The fix must not refuse this.
  const view = validatePortfolioRevision(revision(draft => {
    draft.nodes[1].parent_ref = draft.nodes[0].node_ref;
  }));
  assert.equal(view.node_count, MASTER_NODE_COUNT);
});

test("a portfolio_ref that collides with a node ref refuses", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.portfolio_ref = draft.nodes[0].node_ref;
  }))), "invalid_ref");
});

// --- review corrections: stated metadata must be well formed -----------------
// budget_ceiling = {nonsense: anything} was accepted and hashed into the
// acceptance digest as-is.

test("a malformed budget ceiling refuses instead of being hashed", () => {
  for (const value of [{ nonsense: "anything" }, "12", -1, 1e13, true, []]) {
    assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
      draft.nodes[0].budget_ceiling = value;
    }))), "invalid_metadata", `budget_ceiling ${JSON.stringify(value)} must refuse`);
  }
  const view = validatePortfolioRevision(revision(draft => { draft.nodes[0].budget_ceiling = 0; }));
  assert.equal(view.node_count, MASTER_NODE_COUNT);
});

test("classification slots take a lower-case token and nothing else", () => {
  for (const key of ["authority_class", "effect_class", "data_class"]) {
    for (const value of ["", "Not A Token", "UPPER", 7, { kind: "x" }]) {
      assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
        draft.nodes[0][key] = value;
      }))), "invalid_metadata", `${key}=${JSON.stringify(value)} must refuse`);
    }
  }
  assert.ok(validatePortfolioRevision(revision(draft => { draft.nodes[0].effect_class = "no_effect"; })));
});

test("budget identity, recovery ref and terminal predicate reject malformed values", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].budget_identity = "";
  }))), "invalid_metadata");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].recovery_ref = "no spaces allowed";
  }))), "invalid_ref");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].terminal_predicate = "";
  }))), "invalid_metadata");
  assert.ok(validatePortfolioRevision(revision(draft => {
    draft.nodes[0].budget_identity = "portfolio-budget";
    draft.nodes[0].recovery_ref = "recovery:portfolio-rollback";
    draft.nodes[0].terminal_predicate = "accepted_outcome_present";
  })));
});

test("a stated model floor is a closed nested shape", () => {
  const complete = { provider: "anthropic", model: "opus", version: "5", effort: "high" };
  assert.ok(validatePortfolioRevision(revision(draft => { draft.nodes[0].model_floor = complete; })));
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].model_floor = { ...complete, tier: "premium" };
  }))), "unknown_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    const partial = { ...complete }; delete partial.effort;
    draft.nodes[0].model_floor = partial;
  }))), "invalid_metadata");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].model_floor = { ...complete, version: 5 };
  }))), "invalid_metadata");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].model_floor = "opus";
  }))), "invalid_metadata");
});

// --- review corrections: canonical ordering and JSON safety ------------------

test("edges are ordered by code unit, not by locale collation", () => {
  const preimage = portfolioRevisionPreimage(revision());
  const byCodeUnit = [...preimage.edges].sort((a, b) => {
    if (a.from_node_ref !== b.from_node_ref) return a.from_node_ref < b.from_node_ref ? -1 : 1;
    if (a.to_node_ref === b.to_node_ref) return 0;
    return a.to_node_ref < b.to_node_ref ? -1 : 1;
  });
  assert.deepEqual(preimage.edges, byCodeUnit);
  // Code-unit and locale collation disagree on case: "B" precedes "a" by code
  // unit and follows it under most locales. The acceptance digest must not
  // depend on which ICU build is loaded.
  assert.ok("B" < "a");
  assert.notEqual("B".localeCompare("a") < 0, "B" < "a");
});

test("a cyclic object graph refuses instead of recursing", () => {
  const draft = JSON.parse(JSON.stringify(FIXTURE.revision));
  const loop = { note: "self" };
  loop.loop = loop;
  draft.nodes[0].recovery_ref = loop;
  assert.equal(refusalCode(() => validatePortfolioRevision(draft)), "cyclic_input");
});

test("a sparse array hole refuses rather than hashing null", () => {
  const draft = JSON.parse(JSON.stringify(FIXTURE.revision));
  delete draft.nodes[5];
  assert.equal(refusalCode(() => validatePortfolioRevision(draft)), "unsupported_value");
});

test("symbol, accessor and non-enumerable properties refuse rather than being dropped", () => {
  const withSymbol = JSON.parse(JSON.stringify(FIXTURE.revision));
  withSymbol.nodes[0][Symbol("hidden")] = "dropped by Object.keys";
  assert.equal(refusalCode(() => validatePortfolioRevision(withSymbol)), "unsupported_value");

  const withAccessor = JSON.parse(JSON.stringify(FIXTURE.revision));
  Object.defineProperty(withAccessor.nodes[0], "effect_class", {
    get: () => "no_effect", enumerable: true, configurable: true,
  });
  assert.equal(refusalCode(() => validatePortfolioRevision(withAccessor)), "unsupported_value");

  const withHidden = JSON.parse(JSON.stringify(FIXTURE.revision));
  Object.defineProperty(withHidden.nodes[0], "effect_class", {
    value: "no_effect", enumerable: false, configurable: true, writable: true,
  });
  assert.equal(refusalCode(() => validatePortfolioRevision(withHidden)), "unsupported_value");
});

test("a dependency cycle names its actual members, not innocent downstream nodes", () => {
  let detail;
  try {
    validatePortfolioRevision(revision(draft => {
      // Close a loop between two early nodes. Everything downstream of them is
      // also left unordered, but only these two are cycle members.
      draft.edges.push({
        from_node_ref: "step:wr48-frontier-flowing-production-outcome",
        to_node_ref: "step:portfolio-constitution-accepted",
      });
    }));
    assert.fail("expected a cycle refusal");
  } catch (error) {
    assert.equal(error.code, "cycle");
    detail = error.detail;
  }
  assert.deepEqual([...detail.cycle].sort(), [
    "step:portfolio-constitution-accepted",
    "step:wr48-frontier-flowing-production-outcome",
  ]);
  // The blocked set is reported separately and is strictly larger: every later
  // milestone is unordered too, and none of them is in the cycle.
  assert.ok(detail.blocked_nodes.length > detail.cycle.length);
  assert.ok(detail.blocked_nodes.includes("step:final-portfolio-outcome-reconciliation"));
  assert.ok(!detail.cycle.includes("step:final-portfolio-outcome-reconciliation"));
});

// --- persistence contract: complete metadata and the accepted digest ---------
// The pure graph contract above is unchanged. What follows covers the stricter
// shape storage requires, and the digest a partner actually accepts.

/** The graph plus the explicitly synthetic metadata persistence requires. */
function persistable(mutate) {
  const draft = revision();
  const nodeChildRefs = {};
  for (const node of draft.nodes) {
    // SYNTHETIC. The real node budgets and model floors do not exist in any
    // authenticated source; inventing them would be worse than leaving them
    // absent, so the fixture says so in the value itself.
    node.authority_class = "synthetic_authority";
    node.effect_class = "synthetic_no_effect";
    node.data_class = "synthetic_record_layer";
    node.budget_identity = `synthetic:budget-${node.ordinal}`;
    node.budget_ceiling = node.ordinal === 3 ? 12.5 : (node.ordinal === 5 ? 0.0001 : node.ordinal * 1000);
    node.model_floor = { provider: "synthetic", model: "synthetic", version: "1", effort: "high" };
    node.recovery_ref = `recovery:synthetic-${node.ordinal}`;
    node.terminal_predicate = "synthetic accepted outcome present";
    nodeChildRefs[node.node_ref] = CHILD_PROGRAM_REFS[(node.ordinal - 1) % 4];
  }
  const childInputs = {};
  if (mutate) mutate(draft, nodeChildRefs, childInputs);
  return { draft, nodeChildRefs, childInputs };
}

function persist({ draft, nodeChildRefs, childInputs }) {
  return validatePortfolioRevisionForPersistence(draft, nodeChildRefs, childInputs);
}

test("persistence requires every typed metadata slot the pure contract leaves optional", () => {
  assert.ok(persist(persistable()));
  for (const key of ["authority_class", "effect_class", "data_class", "budget_identity",
    "budget_ceiling", "model_floor", "recovery_ref", "terminal_predicate"]) {
    assert.equal(refusalCode(() => persist(persistable(draft => { delete draft.nodes[0][key]; }))),
      "incomplete_metadata", `missing ${key} must refuse at the persistence boundary`);
  }
});

test("every node must name one of the four child programs", () => {
  assert.equal(refusalCode(() => persist(persistable((draft, ncr) => {
    ncr[draft.nodes[0].node_ref] = "a-fifth-program";
  }))), "child_identity");
  assert.equal(refusalCode(() => persist(persistable((draft, ncr) => {
    delete ncr[draft.nodes[0].node_ref];
  }))), "child_identity");
  assert.equal(refusalCode(() => persist(persistable((draft, ncr) => {
    ncr["step:not-in-this-revision"] = CHILD_PROGRAM_REFS[0];
  }))), "dangling_edge");
});

test("a child that governs no node refuses", () => {
  assert.equal(refusalCode(() => persist(persistable((draft, ncr) => {
    for (const node of draft.nodes) ncr[node.node_ref] = CHILD_PROGRAM_REFS[0];
  }))), "child_identity");
});

test("the accepted digest binds strictly more than the graph digest", () => {
  const base = persist(persistable());
  assert.match(base.graph_digest, /^sha256:[0-9a-f]{64}$/);
  assert.match(base.accepted_digest, /^sha256:[0-9a-f]{64}$/);
  assert.notEqual(base.accepted_digest, base.graph_digest);

  // Each of the three governing facts moves the accepted digest and leaves the
  // graph digest alone. That difference is the whole reason acceptance binds
  // the accepted digest: a hash that omitted these would let what a descendant
  // inherits change under an unchanged signature.
  const childVersion = persist(persistable((draft, ncr, ci) => { ci["assurance-fabric"] = { child_version: 2 }; }));
  const sourceBinding = persist(persistable((draft, ncr, ci) => {
    ci["product-journeys"] = { accepted_plan_ref: "PLAN-22ff72ae82c5-v2" };
  }));
  const membership = persist(persistable((draft, ncr) => {
    ncr[draft.nodes[0].node_ref] = CHILD_PROGRAM_REFS[1];
  }));
  const moved = new Set([base.accepted_digest]);
  for (const [label, view] of [["child_version", childVersion],
    ["accepted_plan_ref", sourceBinding], ["membership", membership]]) {
    assert.equal(view.graph_digest, base.graph_digest, `${label} must not move the graph digest`);
    assert.notEqual(view.accepted_digest, base.accepted_digest, `${label} must move the accepted digest`);
    assert.ok(!moved.has(view.accepted_digest), `${label} must not collide with another change`);
    moved.add(view.accepted_digest);
  }
});

test("child versions are real and per-child, not hardcoded", () => {
  const base = persist(persistable());
  assert.deepEqual(base.child_bindings.map(b => b.child_version), [1, 1, 1, 1]);
  const bumped = persist(persistable((draft, ncr, ci) => {
    ci["foundation-and-control-plane"] = { child_version: 7 };
    ci["rollout-and-retirement"] = { child_version: 3 };
  }));
  assert.deepEqual(bumped.child_bindings.map(b => b.child_version), [7, 1, 1, 3]);
  assert.notEqual(bumped.accepted_digest, base.accepted_digest);
  assert.equal(refusalCode(() => persist(persistable((d, n, ci) => {
    ci["assurance-fabric"] = { child_version: 0 };
  }))), "invalid_revision_version");
});

test("a child hashes its own content and the parent hashes the bindings", () => {
  const base = persist(persistable());
  for (const binding of base.child_bindings) {
    const child = base.children.find(c => c.child_ref === binding.child_ref);
    // The child digest is exactly the hash of the child's own content, so the
    // parent can bind it without either hash depending on the other.
    assert.equal(binding.child_digest, portfolioChildDigest({
      child_ref: child.child_ref, child_version: child.child_version,
      member_node_refs: child.member_node_refs, accepted_plan_ref: child.accepted_plan_ref,
    }));
  }
  const preimage = portfolioAcceptedPreimage(persistable().draft, base.child_bindings);
  assert.deepEqual(Object.keys(preimage).sort(), ["child_bindings", "graph", "schema_version"]);
  assert.equal(preimage.schema_version, PORTFOLIO_ACCEPTED_SCHEMA_VERSION);
  assert.equal(preimage.graph.schema_version, PORTFOLIO_REVISION_SCHEMA_VERSION);
  for (const binding of preimage.child_bindings) {
    assert.deepEqual(Object.keys(binding).sort(),
      ["accepted_plan_ref", "child_digest", "child_ordinal", "child_ref", "child_version"]);
  }
});

test("a substituted child digest, version or ordinal refuses in the accepted preimage", () => {
  const base = persist(persistable());
  const swap = mutate => {
    const bindings = JSON.parse(JSON.stringify(base.child_bindings));
    mutate(bindings);
    return () => portfolioAcceptedPreimage(persistable().draft, bindings);
  };
  assert.equal(refusalCode(swap(b => { b[1].child_ref = "product-journeys"; })), "child_identity");
  assert.equal(refusalCode(swap(b => { b[0].child_ordinal = 2; })), "child_identity");
  assert.equal(refusalCode(swap(b => { b[0].child_digest = "not-a-digest"; })), "invalid_source_digest");
  assert.equal(refusalCode(swap(b => { b[0].child_version = 0; })), "invalid_revision_version");
  assert.equal(refusalCode(swap(b => { b.pop(); })), "child_count");
  assert.equal(refusalCode(swap(b => { b[0].extra = "smuggled"; })), "unknown_field");
});

test("an absent source binding is an explicit null and gaining one changes the hash", () => {
  const none = portfolioChildDigest({
    child_ref: "assurance-fabric", child_version: 1,
    member_node_refs: ["step:a-node"], accepted_plan_ref: null,
  });
  const omitted = portfolioChildDigest({
    child_ref: "assurance-fabric", child_version: 1, member_node_refs: ["step:a-node"],
  });
  assert.equal(none, omitted, "absent and explicit null must mean the same thing");
  const bound = portfolioChildDigest({
    child_ref: "assurance-fabric", child_version: 1,
    member_node_refs: ["step:a-node"], accepted_plan_ref: "PLAN-22ff72ae82c5-v2",
  });
  assert.notEqual(bound, none, "gaining a source binding must change the child hash");
});

test("the whole finite JavaScript budget domain is carried, magnitudes included", () => {
  // An earlier draft refused 1e-7 and 1.5e-7 because PostgreSQL spells them
  // differently. That was magnitude narrowing dressed up as a representation
  // rule: 1e-7 is a value this contract accepts, so the canonicalizer has to
  // render it, not the domain shrink to avoid it. These are the exact cases the
  // numeric-parity finding named, plus the subnormal boundary.
  const carried = [0, 1e-7, 1.5e-7, 1e-6, 1e-5, 12.5, 1e12,
    1, 0.0001, 3.141592653589793, 5e-324];
  const digests = new Set();
  for (const ceiling of carried) {
    const view = persist(persistable(draft => { draft.nodes[0].budget_ceiling = ceiling; }));
    assert.match(view.accepted_digest, /^sha256:[0-9a-f]{64}$/, `${ceiling} must be carried`);
    digests.add(view.accepted_digest);
  }
  assert.equal(digests.size, carried.length, "each distinct ceiling must hash distinctly");

  // Out of the declared range still refuses; that is a domain bound, not a
  // spelling preference.
  assert.equal(refusalCode(() => persist(persistable(draft => {
    draft.nodes[0].budget_ceiling = 1e13;
  }))), "invalid_metadata");
  assert.equal(refusalCode(() => persist(persistable(draft => {
    draft.nodes[0].budget_ceiling = -1;
  }))), "invalid_metadata");
});

test("the module renders budget numbers the way the database must match", () => {
  // The canonical bytes are the contract the PostgreSQL canonicalizer is held
  // to, so the exact spellings are asserted here rather than left implicit.
  for (const [ceiling, spelling] of [[0, "0"], [1e-7, "1e-7"], [1.5e-7, "1.5e-7"],
    [1e-6, "0.000001"], [1e-5, "0.00001"], [12.5, "12.5"], [1e12, "1000000000000"]]) {
    const bytes = portfolioRevisionCanonicalBytes(revision(draft => {
      draft.nodes[0].budget_ceiling = ceiling;
    }));
    assert.ok(bytes.includes(`"budget_ceiling":${spelling}`),
      `${ceiling} must canonicalize as ${spelling}`);
  }
});

test("the cross-layer fixture is exactly what this module produces", () => {
  // The PostgreSQL gate asserts that the database reproduces
  // cross_layer.js_expected for cross_layer's payload. That proof is only worth
  // anything while js_expected really is THIS module's output, so it is
  // re-derived here from the same inputs and compared field by field. If the
  // canonicalizer, the child preimage or the accepted preimage ever changes,
  // this test fails before the database gate can be quietly satisfied by a
  // fixture that drifted to match it.
  const cross = FIXTURE.cross_layer;
  assert.ok(cross, "the fixture must carry a cross_layer block");

  const persisted = revision(draft => {
    for (const node of draft.nodes) Object.assign(node, cross.node_metadata[node.node_ref]);
  });
  const view = validatePortfolioRevisionForPersistence(
    persisted, cross.node_child_refs, cross.child_inputs);

  assert.equal(portfolioRevisionCanonicalBytes(persisted), cross.js_expected.graph_canonical_bytes);
  assert.equal(view.graph_digest, cross.js_expected.graph_digest);
  assert.equal(portfolioAcceptedCanonicalBytes(persisted, view.child_bindings),
    cross.js_expected.accepted_canonical_bytes);
  assert.equal(view.accepted_digest, cross.js_expected.accepted_digest);
  assert.deepEqual(view.child_bindings.map(b => ({ ...b })), cross.js_expected.child_bindings);
  assert.deepEqual(
    Object.fromEntries(view.children.map(c => [c.child_ref, c.member_node_refs])),
    cross.js_expected.child_member_node_refs);

  // Both digests must be recomputable from the recorded bytes alone, so a
  // reviewer can check either one by hand without running this module.
  assert.equal(digest(JSON.parse(cross.js_expected.graph_canonical_bytes)),
    cross.js_expected.graph_digest);
  assert.equal(digest(JSON.parse(cross.js_expected.accepted_canonical_bytes)),
    cross.js_expected.accepted_digest);
});

test("the cross-layer payload spans the whole accepted numeric domain", () => {
  // A parity proof over one spelling proves one spelling. These are the vectors
  // the independent review named, plus the neighbours that make a special-cased
  // renderer fail: 1e-6 sits exactly at the JavaScript exponent threshold and
  // 1e-4 at the PostgreSQL float8 text threshold, so the two layers disagree
  // about how to spell them unless the canonicalizer is doing real work.
  const spellings = FIXTURE.cross_layer.js_expected.budget_ceiling_canonical_text;
  const carried = Object.values(spellings);
  assert.equal(carried.length, MASTER_NODE_COUNT);
  assert.equal(new Set(carried).size, MASTER_NODE_COUNT,
    "every node must render distinctly, or a collision could hide a mismatch");
  for (const required of ["5e-324", "1e-7", "0.000001", "12.5", "3.141592653589793",
    "1000000000000"]) {
    assert.ok(carried.includes(required), `the payload must carry ${required}`);
  }
  // Each recorded spelling is the one the canonical bytes actually contain.
  const bytes = FIXTURE.cross_layer.js_expected.graph_canonical_bytes;
  for (const spelling of carried) {
    assert.ok(bytes.includes(`"budget_ceiling":${spelling}`),
      `the canonical bytes must spell a budget as ${spelling}`);
  }
});

// --- the three write verbs actually call the audit helper correctly ----------
// EVERY TEST ABOVE EXERCISES PURE FUNCTIONS. None of them had ever invoked a
// verb handler, and that gap shipped: all three write verbs called writeEvent
// with an options object where the shared helper in tools.js takes positional
// arguments (client, actor, verb, subjectType, subjectId, fields). The actor,
// the verb and the subject arrived undefined, so the audit insert died on
// event.actor_id not_null_violation and rolled the whole revision back. The
// first live propose-portfolio-revision — the DoctorCre-v5 constitution itself
// — is what found it, on 2026-09-12. Defect 65ed5e3e-db28-498a-a0e7-84ae53658dea.
//
// This drives each handler through a recording double and asserts the call
// SHAPE, which is the thing that was wrong. It fails on the old code at the
// first assertion of every one of the three cases.

import { workPortfolioTools } from "../src/work-portfolio.js";

/** Records what writeEvent was handed, and stands in for the database. */
function harness() {
  const events = [];
  const tools = workPortfolioTools({
    withEnvelope: (_c, _actor, _verb, _args, run) => run(),
    writeEvent: async (client, actor, verb, subjectType, subjectId, fields) => {
      events.push({ client, actor, verb, subjectType, subjectId, fields });
    },
    ToolError: class extends Error {
      constructor(detail) { super(detail.error); Object.assign(this, detail); }
    },
  });
  return { tools, events };
}

const ACTOR = { id: "11111111-1111-4111-8111-111111111111", slug: "test-actor" };
const REVISION_ID = "22222222-2222-4222-8222-222222222222";

/** A client that answers every ops.portfolio_* function with one id. */
const idClient = (id) => ({ query: async () => ({ rows: [{ id }] }) });

test("propose-portfolio-revision writes its audit event with a real actor, verb and subject", async () => {
  const { tools, events } = harness();
  const built = persistable();
  const view = persist(built);
  const args = {
    idempotency_key: "33333333-3333-4333-8333-333333333333",
    portfolio_ref: built.draft.portfolio_ref,
    revision_version: built.draft.revision_version,
    source_digests: built.draft.source_digests,
    graph_digest: view.graph_digest,
    accepted_digest: view.accepted_digest,
    children: CHILD_PROGRAM_REFS.map(ref => ({ child_ref: ref, child_version: 1, accepted_plan_ref: null })),
    nodes: built.draft.nodes.map(node => ({ ...node, child_ref: built.nodeChildRefs[node.node_ref] })),
    edges: built.draft.edges,
  };

  const out = await tools["propose-portfolio-revision"].handler(idClient(REVISION_ID), ACTOR, args);
  assert.equal(out.ok, true);
  assert.equal(out.revision_id, REVISION_ID);

  assert.equal(events.length, 1);
  const [e] = events;
  // The four values the old call shape lost. Each is NOT NULL in event.
  assert.equal(e.actor, ACTOR, "the actor must be forwarded, not replaced by an options object");
  assert.equal(e.actor?.id, ACTOR.id);
  assert.equal(e.verb, "propose-portfolio-revision");
  assert.equal(e.subjectType, "portfolio");
  assert.equal(e.subjectId, REVISION_ID);
  assert.equal(e.fields.idempotency_key, args.idempotency_key);
  assert.equal(e.fields.new.accepted_digest, view.accepted_digest);
});

test("review-portfolio-revision writes its audit event with a real actor, verb and subject", async () => {
  const { tools, events } = harness();
  const args = {
    idempotency_key: "44444444-4444-4444-8444-444444444444",
    revision_id: REVISION_ID,
    reviewed_digest: `sha256:${"a".repeat(64)}`,
    verdict: "pass",
    review_summary: "synthetic",
  };

  const out = await tools["review-portfolio-revision"].handler(
    idClient("55555555-5555-4555-8555-555555555555"), ACTOR, args);
  assert.equal(out.ok, true);

  assert.equal(events.length, 1);
  const [e] = events;
  assert.equal(e.actor, ACTOR, "the actor must be forwarded, not replaced by an options object");
  assert.equal(e.actor?.id, ACTOR.id);
  assert.equal(e.verb, "review-portfolio-revision");
  assert.equal(e.subjectType, "portfolio");
  assert.equal(e.subjectId, REVISION_ID);
  assert.equal(e.fields.new.verdict, "pass");
});

test("accept-portfolio-revision writes its audit event with a real actor, verb and subject", async () => {
  const { tools, events } = harness();
  const args = {
    idempotency_key: "66666666-6666-4666-8666-666666666666",
    revision_id: REVISION_ID,
    accepted_digest: `sha256:${"b".repeat(64)}`,
    review_id: "77777777-7777-4777-8777-777777777777",
  };

  const out = await tools["accept-portfolio-revision"].handler(
    idClient("88888888-8888-4888-8888-888888888888"), ACTOR, args);
  assert.equal(out.ok, true);
  assert.equal(out.accepted, true);

  assert.equal(events.length, 1);
  const [e] = events;
  assert.equal(e.actor, ACTOR, "the actor must be forwarded, not replaced by an options object");
  assert.equal(e.actor?.id, ACTOR.id);
  assert.equal(e.verb, "accept-portfolio-revision");
  assert.equal(e.subjectType, "portfolio");
  assert.equal(e.subjectId, REVISION_ID);
  assert.equal(e.fields.new.accepted_digest, args.accepted_digest);
});

// The human-only verb must stay human-only: a fix to its audit call must not
// quietly widen who can reach it.
test("accept-portfolio-revision is still human-only and authority-only", () => {
  const { tools } = harness();
  assert.equal(tools["accept-portfolio-revision"].humanOnly, true);
  assert.equal(tools["accept-portfolio-revision"].authorityOnly, true);
});
