// Provides migration source helpers for tests.
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../", import.meta.url));
const reader = path.join(repo, "tools/migration_source.py");
const cache = new Map<string, string>();

export function readMigration(file: string | URL): string {
  const name = path.basename(file instanceof URL ? fileURLToPath(file) : file);
  const cached = cache.get(name);
  if (cached !== undefined) return cached;
  const source = execFileSync("python3", [reader, "read", name, "--repo", repo], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  cache.set(name, source);
  return source;
}

export function migrationNames() {
  return execFileSync("python3", [reader, "names", "--repo", repo], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 })
    .trim()
    .split("\n");
}
