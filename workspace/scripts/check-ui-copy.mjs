import { readdir, readFile } from "node:fs/promises";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const blockedVoice = /\b(?:nothing\s+needs?\s+your?\b|what\s+needs?\s+your?\b|needs?\s+you\b|(?:deals?|things?)\s+that\s+need\s+you\b|(?:one|1)\s+thing\s+is\s+broken\b|nothing\s+is\s+(?:stuck|waiting\s+on\s+you)\b|everything\s+is\s+clear\b)/gi;
const textExtensions = new Set([".html", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".json", ".css", ".svg"]);
const ignoredDirectories = new Set([".git", "node_modules", "vendor", "vendors", "third_party"]);

async function textFiles(root) {
  const files = [];
  const visit = async directory => {
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!ignoredDirectories.has(entry.name.toLowerCase())) await visit(resolve(directory, entry.name));
      } else if (entry.isFile() && textExtensions.has(extname(entry.name).toLowerCase())) {
        files.push(resolve(directory, entry.name));
      }
    }
  };

  await visit(root);
  return files;
}

function matches(source, path) {
  const findings = [];
  blockedVoice.lastIndex = 0;
  for (const match of source.matchAll(blockedVoice)) {
    const line = source.slice(0, match.index).split("\n").length;
    findings.push({
      path,
      line,
      message: `banned UI copy: “${match[0].replace(/\s+/g, " ").trim()}”`
    });
  }
  return findings;
}

/** Scan UI source and fixture trees for copy that gives the interface an AI-like voice. */
export async function checkUiCopy(paths) {
  const findings = [];
  for (const suppliedPath of paths) {
    const root = resolve(typeof suppliedPath === "string" ? suppliedPath : fileURLToPath(suppliedPath));
    for (const path of await textFiles(root)) {
      findings.push(...matches(await readFile(path, "utf8"), path));
    }
  }
  return findings;
}

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const paths = process.argv.slice(2);
const roots = paths.length ? paths : [resolve(workspace, "public"), resolve(workspace, "fixtures")];
const findings = await checkUiCopy(roots);
if (findings.length) {
  for (const finding of findings) console.error(`${finding.path}:${finding.line}: ${finding.message}`);
  process.exitCode = 1;
} else {
  console.log(`UI copy lint passed (${roots.length} roots).`);
}
