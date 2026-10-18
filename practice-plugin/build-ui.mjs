import { build } from 'esbuild';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { CATALOG, GROUPS, MARKETS } from './src/planner.mjs';
const bundled = await build({ entryPoints: ['ui/app.mjs'], bundle: true, write: false, format: 'iife', platform: 'browser', minify: true });
const template = await readFile('ui/planner.html', 'utf8');
const css = await readFile('ui/planner.css', 'utf8');
// Replacement callbacks keep SDK strings such as $& literal inside the bundle.
const html = template.replace('/* PLANNER_CSS */', () => css).replace('/* PLANNER_CATALOG */', () => JSON.stringify({ catalog: CATALOG, groups: GROUPS, markets: MARKETS }).replaceAll('<', '\\u003c')).replace('/* PLANNER_SCRIPT */', () => bundled.outputFiles[0].text.replaceAll('</script', '<\\/script'));
await mkdir('.build', { recursive: true });
await writeFile('.build/planner-resource.mjs', `export const plannerHtml = ${JSON.stringify(html)};\n`);
