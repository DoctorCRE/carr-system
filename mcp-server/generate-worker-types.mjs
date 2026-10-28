// Generate Cloudflare Worker type declarations.
import { execFileSync } from "node:child_process";
import { readFileSync, rmSync, writeFileSync } from "node:fs";

const output = ".generated/worker-configuration.d.ts";
rmSync(output, { force: true });
execFileSync("wrangler", ["types", output, "--strict-vars=false", "--env-file", "/dev/null"], {
  stdio: "inherit",
});

let declarations = readFileSync(output, "utf8");
const budgetNamespace = /^[\t ]*AI_NEURON_BUDGET: DurableObjectNamespace<import\("\.\.\/src\/index"\)\.AiNeuronBudget>;/gm;
if ([...declarations.matchAll(budgetNamespace)].length !== 2)
  throw new Error("AI budget namespace declarations changed; review the fetch-only binding.");
declarations = declarations.replace(budgetNamespace, "AI_NEURON_BUDGET: DurableObjectNamespace;");
for (const [site, count] of [
  [/^[\t ]*OAUTH_CONSENT_STATE: DurableObjectNamespace<import\("\.\.\/src\/index"\)\.OAuthConsentState>;/gm, 2],
  [/^[\t ]*\[K in keyof MainModule\]: LoopbackForExport<MainModule\[K\]>/gm, 1],
]) {
  if ([...declarations.matchAll(site)].length !== count) {
    throw new Error("Legacy Durable Object declarations changed; review the brand exception.");
  }
  declarations = declarations.replace(site,
    "// @ts-expect-error Legacy OAuthConsentState lacks the RPC nominal brand.\n$&");
}
writeFileSync(output, declarations);
