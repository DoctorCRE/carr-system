// Resolve sibling imports in relocated source without importing another test suite.
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export function relinkSourceImports(source, directory) {
  return source.replace(/from\s+(["'])\.\/([^"']+)\1/g, (_, _quote, file) =>
    `from ${JSON.stringify(pathToFileURL(join(directory, file)).href)}`);
}
