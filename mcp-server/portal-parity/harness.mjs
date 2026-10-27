// Run one parity stage over the selected inventory rows and return verdict lines.
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { validateMarketStudyPackage } from '../src/market-study-package.js';
import { launchParityBrowser } from './browser.mjs';
import { observeHttp, signIn, sourceFidelity } from './http.mjs';
import { compareScreenshots, observeBrowserProbe } from './observe.mjs';
import { expand, loadProbes, resolveField, sideAliases, siteOf } from './probes.mjs';
import { selectRows } from './rows.mjs';

const MAX_PIXEL_DIFFERENCE = 0.005;
const BROWSER_KINDS = new Set(['dom', 'interaction', 'link', 'media']);

const slash = url => (url.endsWith('/') ? url : `${url}/`);

function differences(reference, candidate) {
  if (reference.missing) return ['reference-lacks-target'];
  if (reference.error) return ['reference-error'];
  if (candidate.missing) return ['candidate-lacks-target'];
  if (candidate.error) return ['candidate-error'];
  const keys = new Set([...Object.keys(reference.facts), ...Object.keys(candidate.facts)]);
  return [...keys].filter(key => reference.facts[key] !== candidate.facts[key]);
}

function verdictFor(probe, aspects) {
  if (!aspects.length) return { verdict: 'PASS', aspects };
  const lacking = aspects.some(aspect => aspect.startsWith('reference-'));
  if (probe.allowed?.length && !lacking) return { verdict: 'ALLOWED', allowed: probe.allowed, aspects };
  return { verdict: 'FAIL', aspects: [...new Set(aspects)] };
}

function cookieFor(base, pair) {
  if (!pair) return [];
  const at = pair.indexOf('=');
  return [{ name: pair.slice(0, at), value: pair.slice(at + 1), url: base }];
}

function summarize(results, stage) {
  const lines = results.map(r => [r.id, r.verdict, r.allowed?.join('+'), r.reason, r.aspects?.length && r.verdict !== 'PASS' ? r.aspects.join(',') : '']
    .filter(Boolean).join(' '));
  const summary = {};
  for (const r of results) {
    const site = siteOf(r.id);
    summary[site] ??= { rows: 0, PASS: 0, ALLOWED: 0, FAIL: 0, UNRUN: 0 };
    summary[site].rows++;
    summary[site][r.verdict]++;
  }
  for (const [site, s] of Object.entries(summary)) {
    lines.push(`${site} stage ${stage}: rows=${s.rows} PASS=${s.PASS} ALLOWED=${s.ALLOWED} FAIL=${s.FAIL} UNRUN=${s.UNRUN}`);
  }
  const ok = results.length > 0 && results.every(r => r.verdict === 'PASS' || r.verdict === 'ALLOWED');
  return { lines, summary, ok };
}

function selected(doc, options) {
  const ids = doc.probes.map(p => p.id).filter(id => !options.site || siteOf(id) === options.site);
  const chosen = new Set(selectRows(options.rows, ids));
  return doc.probes.filter(p => chosen.has(p.id));
}

async function stageOne(doc, probes, options) {
  if (!options.packageFile) throw new Error('stage 1 needs --package');
  const pkg = JSON.parse(await readFile(options.packageFile, 'utf8'));
  const validation = validateMarketStudyPackage(pkg);
  return probes.filter(p => p.fields?.length).map(p => {
    if (!validation.ok) return { id: p.id, verdict: 'FAIL', aspects: ['package-invalid'] };
    const unmapped = p.fields.filter(field => !resolveField(pkg, field));
    return unmapped.length ? { id: p.id, verdict: 'FAIL', aspects: unmapped.map(f => `unmapped:${f}`) } : { id: p.id, verdict: 'PASS', aspects: [] };
  });
}

async function stageZero(doc, probes, options) {
  if (!options.reference || !options.password || !options.sourceDir) throw new Error('stage 0 needs --reference, --source and a password at the prompt');
  const base = slash(options.reference);
  const aliases = sideAliases(doc, options.site, options.aliases);
  const cookie = await signIn(base, options.password);
  const fidelity = await sourceFidelity(base, options.sourceDir, cookie);
  const baseline = {}, results = [];
  for (const probe of probes.filter(p => p.kind === 'http')) {
    if (!probe.stage0 || probe.retired || probe.fault) { results.push({ id: probe.id, verdict: 'UNRUN', reason: probe.retired ? 'retired' : 'not-a-stage-0-probe' }); continue; }
    const observed = await observeHttp(base, probe, aliases, { cookie, password: options.password });
    baseline[probe.id] = observed;
    results.push({ id: probe.id, verdict: observed.unrun ? 'UNRUN' : 'PASS', reason: observed.unrun ? 'needs-session' : 'recorded' });
  }
  if (options.baselineOut) await writeFile(options.baselineOut, `${JSON.stringify(baseline, null, 1)}\n`);
  return { results, prefix: [...fidelity.lines, `files=${fidelity.total} FAIL=${fidelity.failed} UNSERVED=${fidelity.unserved}`], fidelityOk: fidelity.failed === 0 };
}

async function stageTwo(doc, probes, options) {
  if (!options.reference || !options.candidate) throw new Error('stage 2 needs --reference and --candidate');
  const sides = {
    reference: { base: slash(options.reference), overrides: options.aliases },
    candidate: { base: slash(options.candidate), overrides: options.candidateAliases },
  };
  for (const side of Object.values(sides)) {
    side.cookie = options.password ? await signIn(side.base, options.password) : undefined;
  }
  const baseline = options.baselineFile ? JSON.parse(await readFile(options.baselineFile, 'utf8')) : null;
  const browser = probes.some(p => BROWSER_KINDS.has(p.kind) && !p.retired) ? await (options.launch ?? launchParityBrowser)() : null;
  const results = new Map();
  try {
    for (const probe of probes) {
      const site = siteOf(probe.id);
      const aliasFor = side => sideAliases(doc, site, sides[side].overrides);
      if (probe.retired) { results.set(probe.id, { id: probe.id, verdict: 'ALLOWED', allowed: [probe.retired], aspects: ['retired'] }); continue; }
      if (probe.kind === 'replaced') continue;
      if (probe.kind === 'http') {
        if (probe.fault) { results.set(probe.id, { id: probe.id, verdict: 'UNRUN', reason: `fault:${probe.fault}` }); continue; }
        const [reference, candidate] = await Promise.all(['reference', 'candidate'].map(side => (side === 'reference' && baseline?.[probe.id])
          ? baseline[probe.id] : observeHttp(sides[side].base, probe, aliasFor(side), { cookie: sides[side].cookie, password: options.password })));
        if (reference.unrun || candidate.unrun) { results.set(probe.id, { id: probe.id, verdict: 'UNRUN', reason: 'needs-session' }); continue; }
        results.set(probe.id, { id: probe.id, ...verdictFor(probe, differences(reference, candidate)) });
        continue;
      }
      const [reference, candidate] = await Promise.all(['reference', 'candidate'].map(side => observeBrowserProbe(
        browser, sides[side].base, expand(probe, aliasFor(side)), aliasFor(side), { cookies: cookieFor(sides[side].base, sides[side].cookie) })));
      const aspects = differences(reference, candidate);
      if (!aspects.length && reference.shots && candidate.shots) {
        const ratios = await compareScreenshots(browser, reference.shots, candidate.shots);
        for (const [width, ratio] of Object.entries(ratios)) if (ratio > MAX_PIXEL_DIFFERENCE) aspects.push(`screenshot@${width}`);
      }
      if (options.artifacts && (reference.shots || candidate.shots)) {
        await mkdir(options.artifacts, { recursive: true });
        for (const [side, observed] of Object.entries({ reference, candidate })) {
          for (const [width, png] of Object.entries(observed.shots ?? {})) await writeFile(path.join(options.artifacts, `${probe.id}-${side}-${width}.png`), png);
        }
      }
      results.set(probe.id, { id: probe.id, ...verdictFor(probe, aspects) });
    }
  } finally { await browser?.close(); }
  const pkg = options.packageFile ? JSON.parse(await readFile(options.packageFile, 'utf8')) : null;
  for (const probe of probes.filter(p => p.kind === 'replaced' && !p.retired)) {
    const fed = probe.feeds.map(id => results.get(id));
    let result;
    if (!pkg) result = { verdict: 'UNRUN', reason: 'needs-stage-1-package' };
    else if (fed.some(r => !r)) result = { verdict: 'UNRUN', reason: 'feeds-not-in-run' };
    else {
      const aspects = [...probe.fields.filter(f => !resolveField(pkg, f)).map(f => `unmapped:${f}`),
        ...probe.feeds.filter(id => !['PASS', 'ALLOWED'].includes(results.get(id).verdict)).map(id => `feed:${id}`)];
      result = aspects.length ? { verdict: 'FAIL', aspects } : (probe.allowed?.length ? { verdict: 'ALLOWED', allowed: probe.allowed, aspects: [] } : { verdict: 'PASS', aspects: [] });
    }
    results.set(probe.id, { id: probe.id, ...result });
  }
  return probes.map(p => results.get(p.id));
}

export async function runParity(options) {
  const doc = await loadProbes(options.probesFile);
  const probes = selected(doc, options);
  const stage = Number(options.stage);
  let results, prefix = [], extraOk = true;
  if (stage === 0) ({ results, prefix, fidelityOk: extraOk } = await stageZero(doc, probes, options));
  else if (stage === 1) results = await stageOne(doc, probes, options);
  else if (stage === 2) results = await stageTwo(doc, probes, options);
  else throw new Error('--stage must be 0, 1 or 2');
  const { lines, summary, ok } = summarize(results, stage);
  if (options.report) await writeFile(options.report, `${JSON.stringify({ stage, site: options.site ?? 'both', results, summary }, null, 1)}\n`);
  return { lines: [...prefix, ...lines], results, summary, ok: ok && extraOk };
}
