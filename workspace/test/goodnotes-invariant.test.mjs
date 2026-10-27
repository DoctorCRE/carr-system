// Test the Goodnotes share-only invariant.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workspace = new URL("../", import.meta.url);
const readJson = async relative => JSON.parse(await readFile(new URL(relative, workspace), "utf8"));

test("Goodnotes stays share-only interoperability: no state machine transition ever reaches a managed goodnotes_managed state", async () => {
  const machines = await readJson("contracts/state-machines.v1.json");
  const tourRefusals = machines.machines.tour.refused;
  const goodnotesRefusal = tourRefusals.find(item => item.to === "goodnotes_managed");
  assert.ok(goodnotesRefusal, "tour state machine must carry an explicit refusal for the goodnotes_managed transition");
  assert.equal(goodnotesRefusal.from, "*");
  assert.match(goodnotesRefusal.reason, /Goodnotes is optional share interoperability, not a managed runtime state/);

  for (const [name, machine] of Object.entries(machines.machines)) {
    assert.ok(!(machine.allowed ?? []).some(transition => transition.to === "goodnotes_managed"), `${name}: no allowed transition may enter goodnotes_managed`);
    assert.ok(!(machine.states ?? []).includes("goodnotes_managed"), `${name}: goodnotes_managed must not be a declared reachable state`);
  }
});
