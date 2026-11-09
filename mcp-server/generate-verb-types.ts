// Generate TypeScript declarations for MCP verb schemas.
import { mkdir, writeFile } from "node:fs/promises";
import type { JSONSchema4 as JSONSchema, JSONSchema4Type, JSONSchema4TypeName } from "json-schema";

import { compile } from "json-schema-to-typescript";

const toolsModule = "./src/tools.js";
const tools: unknown = await import(toolsModule);
const TOOLS = record(record(tools, "module").TOOLS, "TOOLS");

function record(value: unknown, location: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${location}: invalid schema contract, expected object`);
  }
  return value as Record<string, unknown>;
}

function jsonValue(value: unknown, location: string): JSONSchema4Type {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map((child) => jsonValue(child, location));
  return Object.fromEntries(
    Object.entries(record(value, location)).map(([key, child]) => [key, jsonValue(child, location)]),
  );
}

function schemaType(value: unknown, location: string): JSONSchema4TypeName {
  switch (value) {
    case "string":
    case "number":
    case "integer":
    case "boolean":
    case "null":
    case "object":
    case "array":
    case "any":
      return value;
    default:
      throw new Error(`${location}: invalid schema contract, expected schema type`);
  }
}

function prepareSchema(value: unknown, location: string): JSONSchema {
  const source = record(value, location);
  const schema: JSONSchema = {};
  for (const [keyword, child] of Object.entries(source)) {
    const at = `${location}.${keyword}`;
    switch (keyword) {
      case "type":
        schema.type = Array.isArray(child) ? child.map((type) => schemaType(type, at)) : schemaType(child, at);
        break;
      case "properties":
        schema.properties = Object.fromEntries(
          Object.entries(record(child, at)).map(([name, nested]) => [name, prepareSchema(nested, `${at}.${name}`)]),
        );
        break;
      case "items":
        schema.items = prepareSchema(child, at);
        break;
      case "additionalProperties":
        schema.additionalProperties = typeof child === "boolean" ? child : prepareSchema(child, at);
        break;
      case "oneOf":
      case "anyOf":
        if (!Array.isArray(child)) throw new Error(`${at}: invalid schema contract, expected array`);
        schema[keyword] = child.map((nested, index) => prepareSchema(nested, `${at}[${index}]`));
        break;
      case "required":
        if (!Array.isArray(child) || !child.every((item): item is string => typeof item === "string"))
          throw new Error(`${at}: invalid schema contract, expected strings`);
        schema.required = child;
        break;
      case "enum":
        if (!Array.isArray(child)) throw new Error(`${at}: invalid schema contract, expected array`);
        schema.enum = child.map((item) => jsonValue(item, at));
        break;
      case "const":
      case "default":
        schema[keyword] = jsonValue(child, at);
        break;
      case "description":
      case "pattern":
      case "format":
        if (typeof child !== "string") throw new Error(`${at}: invalid schema contract, expected string`);
        schema[keyword] = child;
        break;
      case "minimum":
      case "maximum":
      case "minItems":
      case "maxItems":
      case "minLength":
      case "maxLength":
      case "maxProperties":
        if (typeof child !== "number" || !Number.isFinite(child))
          throw new Error(`${at}: invalid schema contract, expected finite number`);
        schema[keyword] = child;
        break;
      case "uniqueItems":
        if (typeof child !== "boolean") throw new Error(`${at}: invalid schema contract, expected boolean`);
        schema.uniqueItems = child;
        break;
      default:
        throw new Error(`${location}: unsupported schema keyword ${keyword}`);
    }
  }
  if (schema.oneOf) {
    // A union preserves oneOf only when the alternatives cannot overlap.
    const types = schema.oneOf.map((child) => (child.type === "integer" ? "number" : child.type));
    if (types.some((type) => typeof type !== "string") || new Set(types).size !== types.length) {
      throw new Error(`${location}: unsupported overlapping or untyped oneOf alternatives`);
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
      const schema = record(tool, name).inputSchema;
      if (schema === undefined) throw new Error(`${name} has no inputSchema`);
      return [name, prepareSchema(schema, name)];
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
