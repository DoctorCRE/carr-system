import assert from "node:assert/strict";
import test from "node:test";
import { z } from "zod";
import {
  allowanceSource,
  envelope,
  envelopeShape,
  evidenceClass,
  NOTICE,
  numberType,
  PlanningInputError,
  publicSource,
  rangeSchema,
  sourceSchema,
} from "../src/evidence.ts";

test("default envelope preserves inputs/results and prepends the owner citation", () => {
  const inputs = { area_sf: 2500 };
  const results = { estimate: 42 };
  const actual = envelope(inputs, results);
  assert.equal(actual.inputs_used, inputs);
  assert.equal(actual.inputs_used.area_sf, 2500);
  assert.equal(actual.results.estimate, 42);
  assert.equal(actual.results, results);
  assert.deepEqual(actual, {
    schema_version: "practice-planning/v1",
    calculation_version: "1.0.0",
    inputs_used: inputs,
    input_classification: "owner_input",
    assumptions: [],
    warnings: [],
    missing_inputs: [],
    results,
    limitations: [],
    notice: NOTICE,
    sources: [
      {
        id: "owner-input",
        source_class: "owner input",
        publisher: "Owner-supplied inputs",
        document: "Declared tool inputs",
        url: null,
        jurisdiction: "As supplied; no independent authentication",
        publication_date: null,
        retrieved_at: null,
        units: "Explicit field units; SF, USD, calendar dates, months and equipment capacity as applicable",
        reuse_status: "Returned for this calculation only; no application storage.",
      },
    ],
  });
  assert.deepEqual(envelope(inputs, results), actual);
  assert.notEqual(envelope(inputs, results).warnings, actual.warnings);
});

test("custom envelope retains option arrays and source order including duplicate owner IDs", () => {
  const citation = { ...allowanceSource, id: "owner-input" };
  const options = {
    sources: [citation, allowanceSource],
    assumptions: ["assumed"],
    warnings: ["verify"],
    missing_inputs: ["lease"],
    limitations: ["screening"],
    version: "2.0",
  };
  const actual = envelope({}, ["result"], options);
  assert.equal(actual.calculation_version, "2.0");
  for (const key of ["assumptions", "warnings", "missing_inputs", "limitations"] as const)
    assert.equal(actual[key], options[key]);
  assert.deepEqual(actual.sources.slice(1), options.sources);
  assert.equal(actual.sources.at(0)?.source_class, "owner input");
  const { results, ...base } = actual;
  assert.deepEqual(results, ["result"]);
  assert.deepEqual(z.strictObject(envelopeShape).parse(base), base);
});

test("public citations preserve null and explicit dates and links", () => {
  const source = publicSource("id", "publisher", "document", null, "SF");
  assert.deepEqual(source, {
    id: "id",
    source_class: "published public benchmark",
    publisher: "publisher",
    document: "document",
    url: null,
    jurisdiction: "Design reference; local adoption unverified",
    publication_date: null,
    retrieved_at: "2026-10-01",
    units: "SF",
    reuse_status: "Factual citation and link only; no source prose, floorplans, or bulk dataset redistributed.",
  });
  assert.deepEqual(sourceSchema.parse(source), source);
  const dated = publicSource("id", "publisher", "document", "https://example.com", "SF", "2025-01-01");
  assert.equal(dated.publication_date, "2025-01-01");
  assert.equal(dated.url, "https://example.com");
  assert.deepEqual(sourceSchema.parse(allowanceSource), allowanceSource);
});

test("strict evidence schemas refuse extra fields, invalid classes and nonfinite ranges", () => {
  for (const value of [
    { ...allowanceSource, extra: true },
    { ...allowanceSource, source_class: "unknown" },
    { ...allowanceSource, url: 3 },
  ])
    assert.equal(sourceSchema.safeParse(value).success, false);
  assert.deepEqual(rangeSchema.parse({ low: 4, high: 2 }), { low: 4, high: 2 });
  for (const value of [
    { low: NaN, high: 2 },
    { low: 1, high: Infinity },
    { low: 1, high: 2, extra: true },
  ])
    assert.equal(rangeSchema.safeParse(value).success, false);
  for (const value of ["owner_input", "public_benchmark", "verified_requirement", "planning_assumption"])
    assert.equal(evidenceClass.parse(value), value);
  for (const value of ["CARR estimate", "calculated example", "manufacturer guidance", "adopted requirement"])
    assert.equal(numberType.parse(value), value);
  assert.equal(evidenceClass.safeParse("unknown").success, false);
  assert.equal(numberType.safeParse("unknown").success, false);
});

test("planning input error preserves declared field names and Error behavior", () => {
  const fields = ["receipts.lag_weights", "loan"];
  const error = new PlanningInputError(fields);
  assert.ok(error instanceof Error);
  assert.equal(error.message, "Incompatible declared inputs");
  assert.equal(error.fields, fields);
  assert.deepEqual(error.fields, fields);
});
