// Resolve sibling imports in relocated source without importing another test suite.
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";

export function relinkSourceImports(source, directory) {
  const imports = JSON.parse(readFileSync(join(directory, '../package.json'), 'utf8')).imports;
  source = source.replace(/from\s+(["'])(#[^"']+)\1/g, (_, _quote, specifier) => {
    if (!imports?.[specifier]) throw new Error(`unknown package import: ${specifier}`);
    return `from ${JSON.stringify(pathToFileURL(join(directory, '..', imports[specifier])).href)}`;
  });
  return source.replace(/from\s+(["'])\.\/([^"']+)\1/g, (_, _quote, file) =>
    `from ${JSON.stringify(pathToFileURL(join(directory, file)).href)}`);
}
