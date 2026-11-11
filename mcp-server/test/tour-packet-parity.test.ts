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
    size: { min: 1000, max: 2000, unit: "SF" },
  }));
  const presentation = tourPacketPresentation(properties);
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
    ...presentation.stops.map((p, i) => `${i + 1}. ${p.route_label} · ${p.name} · ${p.address}`),
    presentation.map,
    presentation.properties,
    ...presentation.stops.flatMap((p) => [
      `Stop ${p.route_label}`,
      p.name,
      p.address,
      ...p.facts.flatMap((f) => [f.label, f.value]),
      presentation.notes,
    ]),
  ];
  assert.equal(extracted.stdout.replace(/\s+/g, " ").trim(), expected.join(" "));
});
