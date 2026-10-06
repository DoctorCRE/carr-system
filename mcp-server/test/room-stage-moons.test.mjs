// The moon orbit's geometry, proved against the numbers actually in room.js.
//
// WHY A TEST AND NOT A LOOK. The stage is drawn by browser-only code inside
// boot(), and this repo's unattended guard refuses a local fixture server, so
// "open it and see" is not available to a session running on its own. What can
// be checked without a browser is the thing that would actually go wrong:
// seven moons packed around one node either collide with each other, swallow
// the node they orbit, or fall off the viewBox. The constants are READ OUT OF
// THE SOURCE rather than restated here, so a later tweak to a radius is caught
// by this suite instead of silently breaking the composition.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const SOURCE = fs.readFileSync(new URL("../../dealroom/js/room.js", import.meta.url), "utf8");

function pair(prefix, label = prefix) {
  const m = SOURCE.match(new RegExp(`${prefix}\\s*\\{\\s*rx:\\s*(-?[\\d.]+),\\s*ry:\\s*(-?[\\d.]+)`));
  assert.ok(m, `${label} must still be declared as { rx, ry } in room.js`);
  return { rx: Number(m[1]), ry: Number(m[2]) };
}
function field(name, key) {
  const block = SOURCE.match(new RegExp(`${name}\\s*=\\s*\\{([^}]*)\\}`));
  assert.ok(block, `${name} must still be declared in room.js`);
  const m = block[1].match(new RegExp(`${key}:\\s*(-?[\\d.]+)`));
  assert.ok(m, `${name}.${key} must still be declared`);
  return Number(m[1]);
}

const CENTER = { x: 600, y: 122 };
const LEAD_PULSE = 22;
const MOON_COUNT = 7;   // builder, reviewer, designer, deal steward, intake clerk, marketing ops, system watch

const GEOMETRY = [
  { name: "wide", rings: pair("RINGS_WIDE\\s*=\\s*\\{\\s*inner:", "RINGS_WIDE.inner"), moons: pair("MOONS_WIDE\\s*="),
    pulse: field("MOON_R", "pulse"), label: field("MOON_R", "label"),
    view: [0, 6, 1200, 232] },
  { name: "tight", rings: pair("RINGS_TIGHT\\s*=\\s*\\{\\s*inner:", "RINGS_TIGHT.inner"), moons: pair("MOONS_TIGHT\\s*="),
    pulse: field("MOON_R_TIGHT", "pulse"), label: field("MOON_R_TIGHT", "label"),
    view: [376, 36, 448, 188] },
];

const ringPoint = (ring, index, total) => {
  const angle = (120 + (120 * (index + 1)) / (total + 1)) * (Math.PI / 180);
  return { x: CENTER.x + ring.rx * Math.cos(angle), y: CENTER.y + ring.ry * Math.sin(angle) };
};
const moonPoint = (anchor, moons, index, total) => {
  const angle = (-90 + (360 * index) / total) * (Math.PI / 180);
  return { x: anchor.x + moons.rx * Math.cos(angle), y: anchor.y + moons.ry * Math.sin(angle) };
};

for (const g of GEOMETRY) {
  test(`on the ${g.name} stage, Doc's moons clear each other, clear Doc, and stay on the canvas`, () => {
    const [vx, vy, vw, vh] = g.view;
    // Doc can sit in any slot of an inner ring holding three to six lead desks.
    for (let leads = 3; leads <= 6; leads += 1) {
      for (let slot = 0; slot < leads; slot += 1) {
        const doc = ringPoint(g.rings, slot, leads);
        const points = Array.from({ length: MOON_COUNT }, (_, i) => moonPoint(doc, g.moons, i, MOON_COUNT));
        for (let i = 0; i < MOON_COUNT; i += 1) {
          const a = points[i];
          const b = points[(i + 1) % MOON_COUNT];
          assert.ok(Math.hypot(a.x - b.x, a.y - b.y) > g.pulse * 2,
            `${g.name}: adjacent moons overlap with ${leads} leads, Doc in slot ${slot}`);
          assert.ok(Math.hypot(a.x - doc.x, a.y - doc.y) > LEAD_PULSE + g.pulse,
            `${g.name}: a moon swallows Doc's pulse with ${leads} leads, Doc in slot ${slot}`);
          assert.ok(a.x - g.pulse > vx && a.x + g.pulse < vx + vw,
            `${g.name}: a moon runs off the side with ${leads} leads, Doc in slot ${slot}`);
          assert.ok(a.y - g.pulse > vy && a.y + g.label + 3 < vy + vh,
            `${g.name}: a moon or its name runs off the top or bottom with ${leads} leads, Doc in slot ${slot}`);
        }
      }
    }
  });
}

test("a moon is drawn smaller than a lead desk on both stages", () => {
  for (const g of GEOMETRY) {
    assert.ok(g.pulse < LEAD_PULSE, `${g.name}: the moon pulse must be smaller than a lead desk's`);
  }
  assert.ok(field("MOON_R", "disc") < 17, "a moon's disc must be smaller than a lead desk's 17");
  assert.ok(field("MOON_R_TIGHT", "disc") < field("MOON_R", "disc"),
    "the phone stage draws moons smaller again, because its band is a third the height");
});

test("the moon tether is drawn from Doc's node, never from the wire", () => {
  assert.match(SOURCE, /function moonConnectorPath\(anchor, point\)/,
    "moons must have their own connector that starts at the anchor");
  const body = SOURCE.slice(SOURCE.indexOf("function moonConnectorPath"));
  const line = body.slice(0, body.indexOf("}"));
  assert.ok(!line.includes("CENTER"),
    "a moon tethered to CENTER would put it back in orbit around the wire — the exact lie this replaces");
});
