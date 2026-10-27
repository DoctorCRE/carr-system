// Compile and validate local JSON schemas.
const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);

function resolvePointer(root, reference) {
  if (!reference.startsWith("#/")) throw new Error(`Only local schema references are supported: ${reference}`);
  return reference.slice(2).split("/").reduce((value, token) => value[token.replaceAll("~1", "/").replaceAll("~0", "~")], root);
}

function valueType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (Number.isInteger(value)) return "integer";
  return typeof value === "object" ? "object" : typeof value;
}

export function compileSchema(rootSchema, entrySchema = rootSchema) {
  if (!isObject(rootSchema)) throw new Error("Schema root must be an object");

  const inspected = new Set();
  const supportedValidationKeywords = new Set([
    "$ref", "allOf", "oneOf", "not", "const", "enum", "type", "required", "properties",
    "additionalProperties", "items", "minItems", "maxItems", "uniqueItems", "minLength",
    "maxLength", "pattern", "format", "minimum", "maximum", "minProperties", "maxProperties"
  ]);
  const supportedAnnotationKeywords = new Set([
    "$schema", "$id", "$defs", "title", "description", "default", "examples", "deprecated",
    "readOnly", "writeOnly", "contract", "version", "status", "synthetic_only", "required_surfaces",
    "required_states", "fixture_identity_rule", "canonical_twin_rule", "state_invariants"
  ]);
  function inspectSchema(schema) {
    if (!isObject(schema) || inspected.has(schema)) return;
    inspected.add(schema);
    for (const key of Object.keys(schema)) {
      if (!supportedValidationKeywords.has(key) && !supportedAnnotationKeywords.has(key) && !key.startsWith("x-")) {
        throw new Error(`Unsupported JSON Schema keyword: ${key}`);
      }
    }
    if (schema.$ref) inspectSchema(resolvePointer(rootSchema, schema.$ref));
    const uniqueBy = schema["x-carr-unique-by"];
    if (uniqueBy !== undefined && (typeof uniqueBy !== "string" || !uniqueBy.trim() || uniqueBy !== uniqueBy.trim() || !/^[A-Za-z][A-Za-z0-9._:-]*$/.test(uniqueBy))) {
      throw new Error("x-carr-unique-by must be a non-empty property name");
    }
    if (uniqueBy !== undefined && schema.type !== "array") {
      throw new Error("x-carr-unique-by is only valid on arrays");
    }
    if (uniqueBy !== undefined) {
      const itemSchema = schema.items?.$ref ? resolvePointer(rootSchema, schema.items.$ref) : schema.items;
      if (!isObject(itemSchema) || !isObject(itemSchema.properties) || !Object.prototype.hasOwnProperty.call(itemSchema.properties, uniqueBy)) {
        throw new Error(`x-carr-unique-by item schema declares no ${uniqueBy} property`);
      }
    }
    if (schema.pattern) new RegExp(schema.pattern, "u");
    for (const child of schema.allOf ?? []) inspectSchema(child);
    for (const child of schema.oneOf ?? []) inspectSchema(child);
    if (isObject(schema.not)) inspectSchema(schema.not);
    if (isObject(schema.items)) inspectSchema(schema.items);
    if (isObject(schema.additionalProperties)) inspectSchema(schema.additionalProperties);
    for (const child of Object.values(schema.properties ?? {})) inspectSchema(child);
    for (const child of Object.values(schema.$defs ?? {})) inspectSchema(child);
  }
  inspectSchema(entrySchema);

  function validateNode(schema, value, path, errors, referenceStack = []) {
    if (schema === true || schema === undefined) return;
    if (schema === false) {
      errors.push(`${path}: schema refused the value`);
      return;
    }
    if (schema.$ref) {
      if (referenceStack.includes(schema.$ref)) throw new Error(`Recursive schema reference is unsupported: ${schema.$ref}`);
      validateNode(resolvePointer(rootSchema, schema.$ref), value, path, errors, [...referenceStack, schema.$ref]);
      return;
    }
    if (schema.allOf) for (const child of schema.allOf) validateNode(child, value, path, errors, referenceStack);
    if (schema.oneOf) {
      const matches = schema.oneOf.filter(child => {
        const branchErrors = [];
        validateNode(child, value, path, branchErrors, referenceStack);
        return branchErrors.length === 0;
      }).length;
      if (matches !== 1) errors.push(`${path}: expected exactly one matching oneOf branch, found ${matches}`);
    }
    if (schema.not) {
      const notErrors = [];
      validateNode(schema.not, value, path, notErrors, referenceStack);
      if (notErrors.length === 0) errors.push(`${path}: value matches forbidden not schema`);
    }
    if (schema.const !== undefined && JSON.stringify(value) !== JSON.stringify(schema.const)) errors.push(`${path}: value does not match const`);
    if (schema.enum && !schema.enum.some(option => JSON.stringify(option) === JSON.stringify(value))) errors.push(`${path}: value is outside enum`);

    if (schema.type) {
      const allowed = Array.isArray(schema.type) ? schema.type : [schema.type];
      const actual = valueType(value);
      const compatible = allowed.includes(actual) || (actual === "integer" && allowed.includes("number"));
      if (!compatible) {
        errors.push(`${path}: expected ${allowed.join("|")}, got ${actual}`);
        return;
      }
    }

    if (typeof value === "string") {
      if (schema.minLength !== undefined && value.length < schema.minLength) errors.push(`${path}: shorter than minLength`);
      if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push(`${path}: longer than maxLength`);
      if (schema.pattern && !new RegExp(schema.pattern, "u").test(value)) errors.push(`${path}: does not match pattern`);
      if (schema.format === "date-time" && Number.isNaN(Date.parse(value))) errors.push(`${path}: invalid date-time`);
      if (schema.format === "uuid" && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) errors.push(`${path}: invalid UUID`);
    }
    if (typeof value === "number") {
      if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${path}: below minimum`);
      if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${path}: above maximum`);
    }
    if (Array.isArray(value)) {
      if (schema.minItems !== undefined && value.length < schema.minItems) errors.push(`${path}: fewer than minItems`);
      if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${path}: more than maxItems`);
      if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) errors.push(`${path}: duplicate array items`);
      if (schema["x-carr-unique-by"] !== undefined) {
        const field = schema["x-carr-unique-by"];
        const keys = value.map((item, index) => {
          if (!isObject(item) || !(field in item)) {
            errors.push(`${path}[${index}]: x-carr-unique-by field ${field} is required`);
            return `__missing__${index}`;
          }
          const key = item[field];
          if (key === null || typeof key === "object" || !["string", "number", "boolean"].includes(typeof key) || (typeof key === "number" && !Number.isFinite(key))) {
            errors.push(`${path}[${index}]: x-carr-unique-by field ${field} must be scalar`);
            return `__invalid__${index}`;
          }
          return `${typeof key}:${JSON.stringify(key)}`;
        });
        if (new Set(keys).size !== keys.length) errors.push(`${path}: duplicate values for x-carr-unique-by ${field}`);
      }
      if (schema.items) value.forEach((item, index) => validateNode(schema.items, item, `${path}[${index}]`, errors, referenceStack));
    }
    if (isObject(value)) {
      for (const key of schema.required ?? []) if (!(key in value)) errors.push(`${path}.${key}: required property missing`);
      for (const [key, propertyValue] of Object.entries(value)) {
        if (schema.properties?.[key]) validateNode(schema.properties[key], propertyValue, `${path}.${key}`, errors, referenceStack);
        else if (schema.additionalProperties === false) errors.push(`${path}.${key}: additional property is not allowed`);
        else if (isObject(schema.additionalProperties)) validateNode(schema.additionalProperties, propertyValue, `${path}.${key}`, errors, referenceStack);
      }
      const propertyCount = Object.keys(value).length;
      if (schema.minProperties !== undefined && propertyCount < schema.minProperties) errors.push(`${path}: fewer than minProperties`);
      if (schema.maxProperties !== undefined && propertyCount > schema.maxProperties) errors.push(`${path}: more than maxProperties`);
    }
  }

  return value => {
    const errors = [];
    validateNode(entrySchema, value, "$", errors);
    return { valid: errors.length === 0, errors };
  };
}
