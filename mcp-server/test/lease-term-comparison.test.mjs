import test from "node:test";
import assert from "node:assert/strict";

import { compareLeaseTerms } from "../src/lease-term-comparison.js";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const sample = () => ({
  square_feet: 2500,
  long_term: {
    label: "10 Year Lease",
    term_years: 10,
    base_rent_per_sf: [18, 18.5, 19, 19.5, 20],
    operating_expenses_per_sf: 7,
    free_rent_months: 4,
    free_opex_months: 4,
    buildout_cost_per_sf: 120,
    landlord_ti_per_sf: 50,
  },
  short_term: {
    label: "5 Year Lease",
    term_years: 5,
    base_rent_per_sf: [19, 19.5, 20, 20.5, 21],
    operating_expenses_per_sf: 7,
    free_rent_months: 2,
    free_opex_months: 2,
    buildout_cost_per_sf: 120,
    landlord_ti_per_sf: 30,
  },
  financing: { annual_interest_rate: 0.06, amortization_years: 10 },
});

test("reproduces the CARR 5-vs-10 workbook sample", () => {
  const out = compareLeaseTerms(sample());

  assert.equal(out.calculation_basis, "carr-lease-comparison-5vs10-v1");
  assert.equal(out.comparison_horizon_years, 5);
  assert.equal(out.long_term.cost_of_rent, 325000);
  assert.equal(out.long_term.term_years, 10);
  assert.equal(out.short_term.term_years, 5);
  assert.equal(out.short_term.cost_of_rent, 337500);
  assert.equal(out.long_term.total_free_rent_and_opex, 20833.33);
  assert.equal(out.short_term.total_free_rent_and_opex, 10833.33);
  assert.equal(out.long_term.tenant_ti_contribution, 175000);
  assert.equal(out.long_term.total_interest_on_tenant_ti, 58143.05);
  assert.equal(out.short_term.total_interest_on_tenant_ti, 74755.36);
  assert.equal(out.comparison.additional_cost_of_shorter_term, 89112.3);
  assert.equal(out.comparison.additional_years_of_long_term_year_one_rent, 1.4258);
  assert.equal(out.comparison.direction, "shorter_costs_more");
  assert.equal(out.client_facing_workbook_required, true);
});

test("is registered as a strict read-only pure-computation tool", async () => {
  const tool = TOOLS["compare-lease-terms"];
  assert.ok(tool);
  assert.equal(tool.write, false);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual([...tool.inputSchema.required].sort(),
    ["financing", "long_term", "short_term", "square_feet"]);

  const db = { query: async () => { throw new Error("database must not be called"); } };
  const out = await executeRegisteredTool(db, { id: "joe", human: true },
    "compare-lease-terms", sample());
  assert.equal(out.comparison.additional_cost_of_shorter_term, 89112.3);
  assert.equal(out.calls_models, false);
  assert.equal(out.writes_records, false);
  assert.deepEqual(out.allowed_actions, []);
  assert.equal(JSON.stringify(out).includes("commission"), false);
});

test("dispatcher returns the standard redacted tool error for malformed input", async () => {
  const input = sample();
  input.long_term.term_years = 5;
  input.long_term.label = "CARR-SECRET-CANARY-7F4A";
  const db = { query: async () => { throw new Error("database must not be called"); } };

  await assert.rejects(
    () => executeRegisteredTool(db, { id: "joe", human: true },
      "compare-lease-terms", input),
    (error) => {
      assert.ok(error instanceof ToolError);
      assert.equal(error.payload.error, "invalid_long_term_years");
      assert.equal(JSON.stringify(error.payload).includes("CARR-SECRET-CANARY-7F4A"), false);
      return true;
    },
  );
});

test("zero-interest financing is explicit and stable", () => {
  const input = sample();
  input.financing.annual_interest_rate = 0;
  const out = compareLeaseTerms(input);
  assert.equal(out.long_term.total_interest_on_tenant_ti, 0);
  assert.equal(out.short_term.total_interest_on_tenant_ti, 0);
});

test("reports a signed result when the shorter option costs less", () => {
  const input = sample();
  input.short_term.base_rent_per_sf = [10, 10, 10, 10, 10];
  input.short_term.landlord_ti_per_sf = 120;
  const out = compareLeaseTerms(input);
  assert.ok(out.comparison.additional_cost_of_shorter_term < 0);
  assert.equal(out.comparison.direction, "shorter_costs_less");
});

test("rounds negative half-cents away from zero like the workbook", () => {
  const input = sample();
  input.square_feet = 1;
  input.long_term.base_rent_per_sf = [1, 1, 1, 1, 1];
  input.short_term.base_rent_per_sf = [0.999, 0.999, 0.999, 0.999, 0.999];
  for (const option of [input.long_term, input.short_term]) {
    option.operating_expenses_per_sf = 0;
    option.free_rent_months = 0;
    option.free_opex_months = 0;
    option.buildout_cost_per_sf = 0;
    option.landlord_ti_per_sf = 0;
  }
  input.financing.annual_interest_rate = 0;

  const out = compareLeaseTerms(input);
  assert.equal(out.comparison.additional_cost_of_shorter_term, -0.01);
  assert.equal(out.comparison.direction, "shorter_costs_less");
});

test("rejects malformed, nonfinite, bounded, and authority-bearing inputs without echo", () => {
  const attacks = [
    (x) => { x.canary = "CARR-SECRET-CANARY-7F4A"; },
    (x) => { x.square_feet = true; },
    (x) => { x.square_feet = Number.NaN; },
    (x) => { x.long_term.base_rent_per_sf = [18, 18.5]; },
    (x) => { x.long_term.base_rent_per_sf[2] = Number.POSITIVE_INFINITY; },
    (x) => { x.long_term.free_rent_months = 2.5; },
    (x) => { x.long_term.term_years = 5; },
    (x) => { x.short_term.term_years = 10; },
    (x) => { x.short_term.landlord_ti_per_sf = -1; },
    (x) => { x.financing.annual_interest_rate = 1.01; },
    (x) => { x.financing.actor = "joe"; },
  ];

  for (const mutate of attacks) {
    const input = sample();
    mutate(input);
    let thrown;
    try { compareLeaseTerms(input); } catch (error) { thrown = error; }
    assert.ok(thrown?.payload);
    assert.match(thrown.payload.error, /^invalid_|^unexpected_/);
    assert.equal(JSON.stringify(thrown.payload).includes("CARR-SECRET-CANARY-7F4A"), false);
  }
});

test("detaches accepted inputs and outputs", () => {
  const input = sample();
  const first = compareLeaseTerms(input);
  input.long_term.base_rent_per_sf[0] = 999;
  first.long_term.base_rent_schedule[0].annual_base_rent = 999;
  const second = compareLeaseTerms(sample());

  assert.equal(first.comparison.additional_cost_of_shorter_term, 89112.3);
  assert.equal(second.long_term.base_rent_schedule[0].annual_base_rent, 45000);
});
