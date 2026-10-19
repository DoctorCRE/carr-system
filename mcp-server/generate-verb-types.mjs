import { mkdir, writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";
import { TOOLS } from "./src/tools.js";

// Scalar validation remains the runtime schema validator's responsibility.
// Accept only this known vocabulary; new schema constructs require a mapping here.
const supported = new Set([
  "type", "properties", "required", "additionalProperties", "items", "enum", "const", "oneOf", "anyOf",
  "description", "default", "minimum", "maximum", "minItems", "maxItems", "pattern",
  "minLength", "maxLength", "format", "uniqueItems", "maxProperties",
]);
function prepareSchema(schema, location) {
  if (typeof schema === "boolean") return schema;
  for (const keyword of Object.keys(schema)) {
    if (!supported.has(keyword)) throw new Error(`${location}: unsupported schema keyword ${keyword}`);
  }
  if (schema.oneOf) {
    // A union preserves oneOf only when the alternatives cannot overlap.
    const types = schema.oneOf.map((child) => child.type === "integer" ? "number" : child.type);
    if (types.some((type) => typeof type !== "string") || new Set(types).size !== types.length) {
      throw new Error(`${location}: unsupported overlapping or untyped oneOf alternatives`);
    }
  }
  for (const [name, child] of Object.entries(schema.properties ?? {})) {
    schema.properties[name] = prepareSchema(child, `${location}.properties.${name}`);
  }
  for (const keyword of ["items", "additionalProperties"]) {
    if (schema[keyword] && typeof schema[keyword] === "object") {
      schema[keyword] = prepareSchema(schema[keyword], `${location}.${keyword}`);
    }
  }
  for (const keyword of ["oneOf", "anyOf"]) {
    for (const [index, child] of (schema[keyword] ?? []).entries()) {
      schema[keyword][index] = prepareSchema(child, `${location}.${keyword}[${index}]`);
    }
  }
  if (schema.type === "object" && schema.additionalProperties === false && !Object.keys(schema.properties ?? {}).length) {
    if (schema.required?.length || ["enum", "const", "oneOf", "anyOf"].some((key) => key in schema)) {
      throw new Error(`${location}: unsupported constrained closed empty object`);
    }
    return { tsType: "Record<string, never>" };
  }
  return schema;
}

const properties = Object.fromEntries(Object.keys(TOOLS).sort().map((name) => {
  if (!TOOLS[name].inputSchema) throw new Error(`${name} has no inputSchema`);
  return [name, prepareSchema(structuredClone(TOOLS[name].inputSchema), name)];
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
