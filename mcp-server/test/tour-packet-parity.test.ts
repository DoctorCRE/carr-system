import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { tourPacketPresentation } from "../src/tour-packet-template.ts";

async function renderer() {
  const module: unknown = await import(new URL("../src/tour-pdf-renderer.js", import.meta.url).href);
  assert.ok(
    module &&
      typeof module === "object" &&
      "renderTourPacketPdf" in module &&
      typeof module.renderTourPacketPdf === "function",
  );
  return module.renderTourPacketPdf;
}
test("real PDF text equals the versioned web presentation for three shuffled properties", async () => {
  const properties = [3, 1, 2].map((n) => ({
    property_ref: `property:public:synthetic_stop_000${n}`,
    route_sequence: n,
    route_label: String(n),
    name: `Property ${n}`,
    address: `${n} Example Street`,
    appointment_start: n === 1 ? "2031-03-04T14:00:00.123456+00:00" : null,
    appointment_end: n === 1 ? "2031-03-04T14:30:00.123456+00:00" : null,
    public_packet_note: n === 1 ? "Please meet in the lobby" : null,
    size: { min: 1000, max: 2000, unit: "SF" },
  }));
  const presentation = tourPacketPresentation(properties);
  assert.equal(
    presentation.stops[0]?.appointment,
    "2031-03-04T14:00:00.123456+00:00 to 2031-03-04T14:30:00.123456+00:00",
  );
  assert.equal(presentation.stops[1]?.appointment, null);
  const render = await renderer();
  const rendered = await render(
    { as_of: "2026-08-27T12:00:00Z", properties },
    {
      regular: new Uint8Array(await readFile(new URL("../assets/lato-regular.ttf", import.meta.url))),
      bold: new Uint8Array(await readFile(new URL("../assets/lato-bold.ttf", import.meta.url))),
    },
  );
  assert.ok(rendered && typeof rendered === "object" && "bytes" in rendered && rendered.bytes instanceof Uint8Array);
  const { spawnSync } = await import("node:child_process");
  const extracted = spawnSync("pdftotext", ["-", "-"], { input: rendered.bytes, encoding: "utf8" });
  assert.equal(extracted.status, 0, extracted.stderr);
  const expected = [
    presentation.brand,
    presentation.title,
    presentation.summary,
    presentation.overview,
    ...presentation.stops.map(
      (p, i) => `${i + 1}. ${p.route_label} · ${p.name} · ${p.address}${p.appointment ? ` · ${p.appointment}` : ""}`,
    ),
    presentation.map,
    presentation.properties,
    ...presentation.stops.flatMap((p) => [
      `Stop ${p.route_label}`,
      p.name,
      p.address,
      ...(p.appointment ? [p.appointment] : []),
      ...p.facts.flatMap((f) => [f.label, f.value]),
      ...(p.public_packet_note ? [p.public_packet_note] : []),
      presentation.notes,
    ]),
  ];
  assert.equal(extracted.stdout.replace(/\s+/g, " ").trim(), expected.join(" "));
});

test("public notes are explicit and internal notes cannot enter rendering", async () => {
  const loaded: unknown = await import(new URL("../src/tour-packet-render.js", import.meta.url).href);
  assert.ok(
    loaded &&
      typeof loaded === "object" &&
      "renderTourPacket" in loaded &&
      typeof loaded.renderTourPacket === "function",
  );
  const property = {
    property_ref: "property:public:synthetic_stop_0001",
    route_sequence: 1,
    route_label: "A",
    name: "Office",
    address: "1 Example Street",
  };
  const renderPacket = loaded.renderTourPacket;
  const render = (stop: object) => renderPacket({ as_of: "2031-03-04T14:00:00Z", properties: [stop] });
  const result = render({ ...property, public_packet_note: "Please meet in the lobby" });
  assert.equal(result.contractVersion, "2.1.0");
  assert.equal(result.rendererVersion, "2.0.0");
  assert.match(result.html, /Please meet in the lobby/);
  assert.doesNotMatch(render(property).html, /Appointment|Please meet/);
  assert.throws(() => render({ ...property, internal_notes: "PRIVATE_CANARY" }));
  assert.throws(() => render({ ...property, public_packet_note: "Internal note: PRIVATE_CANARY" }));
  assert.throws(() => render({ ...property, appointment_start: "2031-03-04T14:00:00Z" }));
});
