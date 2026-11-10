// Resolve relative imports in relocated source without importing another test suite.
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export function relinkSourceImports(source: string, directory: string) {
  return source.replace(
    /from\s+(["'])(\.\.?\/[^"']+)\1/g,
    (_: string, _quote: string, file: string) => `from ${JSON.stringify(pathToFileURL(join(directory, file)).href)}`,
  );
}
