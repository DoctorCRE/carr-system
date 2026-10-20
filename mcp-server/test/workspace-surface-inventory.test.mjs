import test from "node:test";
import assert from "node:assert/strict";
import { AUTHENTICATED_SURFACES, WORKSPACE_ROUTES } from "../src/workspace-surface-inventory.js";
import { isDealroomRequest } from "../src/dealroom-web.js";

test("the Atlas surface inventory names authenticated CARR browser routes", () => {
  assert.deepEqual([...WORKSPACE_ROUTES].sort(),
    ["/", "/clients", "/deals", "/leads", "/queue.html", "/room.html", "/system-work.html", "/vendors"]);
  for (const route of WORKSPACE_ROUTES) {
    assert.equal(isDealroomRequest(new Request(`https://app.doctorcre.com${route}`),
      { APP_HOST: "app.doctorcre.com" }), true, route);
  }
  assert.equal(new Set(AUTHENTICATED_SURFACES.map(surface => surface.asset)).size, 7);
});
