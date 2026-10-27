#!/usr/bin/env node
// ./run.sh portal-parity: compare a candidate portal with a reference site, row by row.
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { runParity } from './harness.mjs';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const USAGE = `usage: ./run.sh portal-parity --site <REF-A|REF-V> --stage <0|1|2> --reference <url> --candidate <url>
  [--probes <file>] [--rows <ids>] [--aliases <file>] [--candidate-aliases <file>] [--package <file>]
  [--source <dir>] [--manifest <file>] [--baseline <file>] [--baseline-out <file>] [--report <file>] [--artifacts <dir>] [--sign-in | --password-stdin] [--headed]
Stage 0 hashes every path in --manifest (the reference Worker's generated route manifest) against --source.
Stage 0 and --sign-in read the password at a hidden prompt (or as one line on stdin with --password-stdin); never pass one as an argument.
The browser is headless; --headed opens a visible window for debugging only.
Alias files, baselines, reports and artifacts hold run data: keep them outside the repository.`;

async function readStdinLine() {
  let text = '';
  for await (const chunk of process.stdin) text += chunk;
  return text.split(/\r?\n/)[0];
}

async function prompt(question) {
  if (!process.stdin.isTTY) return undefined;
  process.stderr.write(question);
  process.stdin.setRawMode(true);
  process.stdin.resume();
  let value = '';
  return new Promise(resolve => {
    const onData = chunk => {
      for (const char of chunk.toString('utf8')) {
        if (char === '\r' || char === '\n') { process.stdin.setRawMode(false); process.stdin.pause(); process.stdin.off('data', onData); process.stderr.write('\n'); resolve(value); return; }
        if (char === '\u0003') process.exit(130);
        value = char === '\u007f' ? value.slice(0, -1) : value + char;
      }
    };
    process.stdin.on('data', onData);
  });
}

// Local run data must never land in the public repository.
function outsideRepo(file, flag) {
  if (file && path.resolve(file).startsWith(`${REPO}${path.sep}`)) throw new Error(`${flag} must point outside the repository`);
  return file;
}

async function main() {
  const { values } = parseArgs({
    options: Object.fromEntries(['site', 'stage', 'reference', 'candidate', 'probes', 'rows', 'aliases', 'candidate-aliases', 'package',
      'source', 'manifest', 'baseline', 'baseline-out', 'report', 'artifacts'].map(name => [name, { type: 'string' }])
      .concat([['password-stdin', { type: 'boolean' }], ['sign-in', { type: 'boolean' }], ['headed', { type: 'boolean' }], ['help', { type: 'boolean' }]])),
  });
  if (values.help || !values.stage) { console.log(USAGE); return values.help ? 0 : 2; }
  if (values.site && !['REF-A', 'REF-V'].includes(values.site)) throw new Error('--site must be REF-A or REF-V');
  const readAliases = async file => (file ? JSON.parse(await readFile(outsideRepo(file, '--aliases'), 'utf8')) : undefined);
  const needsPassword = values.stage === '0' || values['sign-in'];
  const password = values['password-stdin'] ? await readStdinLine()
    : needsPassword ? await prompt('Site password (not echoed): ') : undefined;
  const run = await runParity({
    stage: values.stage, site: values.site, reference: values.reference, candidate: values.candidate,
    probesFile: values.probes ?? path.join(REPO, 'client-portal/parity/probes.v1.json'), rows: values.rows,
    aliases: await readAliases(values.aliases), candidateAliases: await readAliases(values['candidate-aliases']),
    packageFile: values.package, sourceDir: values.source, manifest: values.manifest, baselineFile: values.baseline,
    baselineOut: outsideRepo(values['baseline-out'], '--baseline-out'), report: outsideRepo(values.report, '--report'),
    artifacts: outsideRepo(values.artifacts, '--artifacts'), password: password || undefined, headed: values.headed,
  });
  for (const line of run.lines) console.log(line);
  return run.ok ? 0 : 1;
}

main().then(code => { process.exitCode = code; }, error => { console.error(`portal-parity: ${error.message}`); process.exitCode = 2; });
