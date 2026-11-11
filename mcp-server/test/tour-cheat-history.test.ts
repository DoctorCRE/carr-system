import assert from "node:assert/strict";
import test from "node:test";
import { createTourInternalWebHandler } from "../src/tour-internal-web.ts";

const tour = "11111111-1111-4111-8111-111111111111";
test("history read requires session and rejects caller authority", async () => {
  let reads = 0;
  const handler = createTourInternalWebHandler({
    readCheatSheetHistoryFn: async ({ input }) => {
      reads++;
      assert.deepEqual(input, { tour_id: tour });
      return { ok: true, data: { revisions: [] } };
    },
  });
  const env = { APP_HOST: "app.doctorcre.com" },
    actor = { id: "joe", slug: "joe" },
    session = { csrfToken: "synthetic" };
  const request = (query: string) => new Request(`https://app.doctorcre.com/api/tours/cheat-sheet/history/v1?${query}`);
  assert.equal((await handler.fetch(request(`tour_id=${tour}`), env, {})).status, 401);
  assert.equal((await handler.fetch(request(`tour_id=${tour}&actor=other`), env, {}, actor, session)).status, 400);
  const response = await handler.fetch(request(`tour_id=${tour}`), env, {}, actor, session);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { data: { revisions: [] }, csrf_token: "synthetic" });
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(reads, 1);
});
