import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../", import.meta.url));
const reader = path.join(repo, "tools/migration_source.py");
const cache = new Map();

export function readMigration(file) {
  const name = path.basename(file instanceof URL ? fileURLToPath(file) : file);
  if (!cache.has(name)) {
    cache.set(name, execFileSync("python3", [reader, "read", name, "--repo", repo], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
  }
  return cache.get(name);
}

export function migrationNames() {
  return execFileSync("python3", [reader, "names", "--repo", repo], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }).trim().split("\n");
}
