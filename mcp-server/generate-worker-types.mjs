import { execFileSync } from "node:child_process";
import { readFileSync, rmSync, writeFileSync } from "node:fs";

const output = ".generated/worker-configuration.d.ts";
rmSync(output, { force: true });
execFileSync("wrangler", ["types", output, "--strict-vars=false", "--env-file", "/dev/null"], {
  stdio: "inherit",
});

let declarations = readFileSync(output, "utf8");
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
