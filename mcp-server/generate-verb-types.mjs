import { mkdir, writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";
import { TOOLS } from "./src/tools.js";

const properties = Object.fromEntries(Object.keys(TOOLS).sort().map((name) => {
  if (!TOOLS[name].inputSchema) throw new Error(`${name} has no inputSchema`);
  return [name, TOOLS[name].inputSchema];
}));
const declarations = await compile({
  type: "object", properties, required: Object.keys(properties), additionalProperties: false,
}, "VerbInputs", {
  bannerComment: "/* Generated from TOOLS inputSchema by npm run generate:types. */",
  unknownAny: true,
  $refOptions: { resolve: { file: false, http: false } },
});
const output = new URL("./.generated/", import.meta.url);
await mkdir(output, { recursive: true });
await writeFile(new URL("verb-inputs.d.ts", output), declarations);
