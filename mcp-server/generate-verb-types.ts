// Generate TypeScript declarations for MCP verb schemas.
import { mkdir, writeFile } from "node:fs/promises";
import type { JSONSchema4 as JSONSchema } from "json-schema";

// Runtime imports keep the legacy Worker graph outside the strict Node project.
const compilerModule = "json-schema-to-typescript";
const {
  compile,
}: {
  compile(
    schema: JSONSchema,
    name: string,
    options: {
      bannerComment: string;
      unknownAny: boolean;
      $refOptions: { resolve: { file: false; http: false } };
    },
  ): Promise<string>;
} = await import(compilerModule);
const toolsModule = "./src/tools.js";
const { TOOLS }: { TOOLS: Record<string, { inputSchema?: JSONSchema }> } = await import(toolsModule);

// Scalar validation remains the runtime schema validator's responsibility.
// Accept only this known vocabulary; new schema constructs require a mapping here.
const supported = new Set([
  "type",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "enum",
  "const",
  "oneOf",
  "anyOf",
  "description",
  "default",
  "minimum",
  "maximum",
  "minItems",
  "maxItems",
  "pattern",
  "minLength",
  "maxLength",
  "format",
  "uniqueItems",
  "maxProperties",
]);
function prepareSchema(schema: JSONSchema, location: string): JSONSchema {
  if (typeof schema === "boolean") return schema;
  for (const keyword of Object.keys(schema)) {
    if (!supported.has(keyword)) throw new Error(`${location}: unsupported schema keyword ${keyword}`);
  }
  if (schema.oneOf) {
    // A union preserves oneOf only when the alternatives cannot overlap.
    const types = schema.oneOf.map((child) => (child.type === "integer" ? "number" : child.type));
    if (types.some((type) => typeof type !== "string") || new Set(types).size !== types.length) {
      throw new Error(`${location}: unsupported overlapping or untyped oneOf alternatives`);
    }
  }
  const fields = schema.properties ?? {};
  for (const [name, child] of Object.entries(fields)) {
    fields[name] = prepareSchema(child, `${location}.properties.${name}`);
  }
  for (const keyword of ["items", "additionalProperties"] as const) {
    if (schema[keyword] && typeof schema[keyword] === "object") {
      schema[keyword] = prepareSchema(schema[keyword] as JSONSchema, `${location}.${keyword}`);
    }
  }
  for (const keyword of ["oneOf", "anyOf"] as const) {
    const alternatives = schema[keyword] ?? [];
    for (const [index, child] of alternatives.entries()) {
      alternatives[index] = prepareSchema(child, `${location}.${keyword}[${index}]`);
    }
  }
  if (
    schema.type === "object" &&
    schema.additionalProperties === false &&
    !Object.keys(schema.properties ?? {}).length
  ) {
    if (
      (Array.isArray(schema.required) && schema.required.length) ||
      ["enum", "const", "oneOf", "anyOf"].some((key) => key in schema)
    ) {
      throw new Error(`${location}: unsupported constrained closed empty object`);
    }
    return { tsType: "Record<string, never>" };
  }
  return schema;
}

const properties = Object.fromEntries(
  Object.entries(TOOLS)
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([name, tool]) => {
      if (!tool.inputSchema) throw new Error(`${name} has no inputSchema`);
      return [name, prepareSchema(structuredClone(tool.inputSchema), name)];
    }),
);
const declarations = await compile(
  {
    type: "object",
    properties,
    required: Object.keys(properties),
    additionalProperties: false,
  },
  "VerbInputs",
  {
    bannerComment: "/* Generated from TOOLS inputSchema by npm run generate:types. */",
    unknownAny: true,
    $refOptions: { resolve: { file: false, http: false } },
  },
);
const output = new URL("./.generated/", import.meta.url);
await mkdir(output, { recursive: true });
await writeFile(new URL("verb-inputs.d.ts", output), declarations);
