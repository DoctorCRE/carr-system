import { readdir, readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export async function discoverSuites(directory: string, inventory: unknown): Promise<string[]> {
  if (
    typeof inventory !== "object" ||
    inventory === null ||
    Array.isArray(inventory) ||
    !("expected" in inventory) ||
    typeof inventory.expected !== "number" ||
    !Number.isInteger(inventory.expected) ||
    inventory.expected < 1
  )
    throw new Error("Invalid checked-in unit suite count");
  const expected = inventory.expected;
  const externalValue = "external" in inventory ? inventory.external : [];
  if (!Array.isArray(externalValue) || !externalValue.every((name: unknown) => typeof name === "string"))
    throw new Error("Invalid checked-in unit external suites");
  const external: string[] = externalValue;
  const files: string[] = [];
  const omitted: string[] = [];
  async function visit(relative = ""): Promise<void> {
    for (const entry of await readdir(path.join(directory, relative), { withFileTypes: true })) {
      const name = path.posix.join(relative, entry.name);
      if (entry.isDirectory()) {
        await visit(name);
        continue;
      }
      if (!entry.isFile() || !/\.(?:[cm]?[jt]sx?)$/.test(name)) continue;
      if (!relative && /\.test\.(?:mjs|js|ts)$/.test(name)) {
        files.push(name);
        continue;
      }
      // Separate database acceptance scripts are owned by ops/ci-gates.py.
      if (external.includes(name)) continue;
      const source = await readFile(path.join(directory, name), "utf8");
      if (
        /\.(?:test|spec)\.(?:[cm]?[jt]sx?)$/.test(name) ||
        (/['"]node:test['"]/.test(source) && /\b(?:test|describe|it)\s*(?:\.\s*(?:skip|only|todo))?\s*\(/.test(source))
      )
        omitted.push(name);
    }
  }
  await visit();
  if (omitted.length)
    throw new Error(
      `Unit discovery would omit ${omitted.sort().join(", ")}; put unit suites directly in test/ and name them *.test.mjs, *.test.js or *.test.ts.`,
    );
  // A floor, not an exact count: independent PRs that add suites must not edit one shared line.
  if (files.length < expected)
    throw new Error(
      `Unit suite inventory mismatch: found ${files.length}, expected at least ${expected}; drop ${expected - files.length}. Check deleted, renamed or nested suites and lower the checked-in floor deliberately.`,
    );
  return files.sort();
}

if (process.argv[1] && (await realpath(process.argv[1]).catch(() => null)) === fileURLToPath(import.meta.url)) {
  const root = path.dirname(fileURLToPath(import.meta.url));
  const inventory: unknown = JSON.parse(await readFile(path.join(root, "suite-inventory.json"), "utf8"));
  console.log(JSON.stringify(await discoverSuites(path.join(root, "test"), inventory)));
}
