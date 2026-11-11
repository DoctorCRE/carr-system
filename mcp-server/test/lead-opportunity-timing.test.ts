import assert from "node:assert/strict";
import test from "node:test";
import * as timing from "../src/lead-opportunity-timing.ts";
import { executeRegisteredTool } from "./helpers/legacy-test-registry.ts";

const id = "30000000-0000-0000-0000-000000000001";
const bad = {
  contract: "lead-opportunity-timing.v0",
  record_added_at: "not-an-instant",
  status: "unknown",
  events: [{ date: "2026-02-30" }],
};

test("registered lead-board refuses the reviewer's malformed timing row on board and detail", async () => {
  for (const detail of [false, true]) {
    let calls = 0;
    const lead = { id, stage: "new", suppressed: false, opportunity_timing: bad };
    const c = {
      query: async () => {
        calls++;
        return {
          rows: [
            {
              leads: detail ? [] : [lead],
              detail: detail ? lead : null,
            },
          ],
        };
      },
    };
    await assert.rejects(
      executeRegisteredTool(c, { id, slug: "joe", human: true }, "lead-board", {
        workspace: "leads",
        ...(detail ? { lead_id: id } : {}),
      }),
      /invalid_lead_opportunity_timing/,
    );
    assert.equal(calls, 1);
  }
});

test("timing parser validates version, calendar days, instants and support invariants", () => {
  assert.ok("parseLeadOpportunityTiming" in timing);
  const parse = timing.parseLeadOpportunityTiming as (value: unknown) => unknown;
  const unknown = {
    contract: "lead-opportunity-timing.v1",
    record_added_at: "2026-11-01T12:00:00Z",
    status: "unknown",
    events: [],
  };
  const event = {
    date: "2026-10-01",
    date_type: "date",
    date_basis: "enumeration",
    source: "nppes",
    record_key: "1999999911",
    revision: "123",
    source_url: "https://npiregistry.cms.hhs.gov/provider-view/1999999911",
    published_on: "2026-11-02",
    observed_at: "2026-11-02T12:00:00.123456+00:00",
    meaning: "NPI enumerated",
    uncertainty: "Practice opening and real-estate decision unverified",
  };
  const supported = { ...unknown, status: "supported", events: [event] };
  assert.deepEqual(parse(unknown), unknown);
  assert.deepEqual(parse(supported), supported);
  for (const value of [
    bad,
    null,
    {},
    { ...unknown, contract: "lead-opportunity-timing.v0" },
    { ...unknown, record_added_at: "2026-11-01" },
    { ...unknown, record_added_at: "2026-02-30T12:00:00Z" },
    { ...unknown, record_added_at: "2026-11-01T24:00:00Z" },
    { ...unknown, status: "supported" },
    { ...supported, status: "unknown" },
    ...Object.entries({
      date: "2026-02-30",
      date_type: "instant",
      published_on: "2026-02-30",
      observed_at: "not-an-instant",
      source: "other",
      date_basis: "filing",
      revision: "",
      meaning: null,
      uncertainty: null,
      source_url: null,
      record_key: null,
    }).map(([key, value]) => ({ ...supported, events: [{ ...event, [key]: value }] })),
  ])
    assert.throws(() => parse(value), /invalid_lead_opportunity_timing/, JSON.stringify(value));
});
