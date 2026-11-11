import assert from "node:assert/strict";
import test from "node:test";
import { vendorMarketTools } from "../src/vendor-market-registry.ts";
import type { Db } from "../src/verb-contract.ts";

const read = vendorMarketTools({
  canExercisePartnerAuthority: () => false,
  organizationTenantForActor: () => "carr-internal",
})["read-vendor-markets-v1"].handler;
const unused: Db = {
  query: async () => {
    throw new Error("database must not be touched");
  },
};
test("vendor market read refuses unauthenticated and machine callers before SQL", async () => {
  for (const actor of [{ id: "anonymous" }, { id: "machine", slug: "joe", human: false }])
    await assert.rejects(read(unused, actor, {}), /partner_required/);
});
test("vendor market read refuses invalid selectors before SQL", async () => {
  await assert.rejects(
    read(unused, { id: "joe", slug: "joe", human: true }, { market: "  " }),
    /invalid_vendor_market_arguments/,
  );
});
