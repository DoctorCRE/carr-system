// Test Doc OAuth discovery and existing authorization metadata.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { build } from "esbuild";

// Only the Cloudflare class type is shimmed. Routing, discovery and missing
// credential refusal exercise the installed OAuth provider's real code.
async function worker() {
  const result = await build({
    entryPoints: [new URL("../src/index.js", import.meta.url).pathname],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    packages: "bundle",
    loader: { ".ttf": "binary" },
    banner: {
      js: "import {createRequire} from 'node:module'; const require=createRequire(process.cwd()+'/package.json');",
    },
    plugins: [
      {
        name: "cloudflare-type",
        setup(b) {
          b.onResolve({ filter: /^cloudflare:workers$/ }, () => ({ path: "cloudflare-type", namespace: "fixture" }));
          b.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents: "export class WorkerEntrypoint {}" }));
        },
      },
    ],
  });
  assert.ok(result.outputFiles[0]);
  const loaded: unknown = await import(
    `data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`
  );
  assert.ok(typeof loaded === "object" && loaded !== null && "default" in loaded);
  const current = loaded.default;
  assert.ok(
    typeof current === "object" && current !== null && "fetch" in current && typeof current.fetch === "function",
  );
  const fetch = current.fetch;
  return {
    fetch: async (request: Request, env: object, ctx: object): Promise<Response> => {
      const response: unknown = await fetch.call(current, request, env, ctx);
      assert.ok(response instanceof Response);
      return response;
    },
  };
}
const current = await worker();
const env = () => ({});
const ctx = { waitUntil() {} };
const request = (path: string) => new Request(`https://api.doctorcre.com${path}`);

test("Doc OAuth discovery names the exact resource and existing authorization server", async () => {
  const result = await current.fetch(request("/.well-known/oauth-protected-resource/doc/mcp"), env(), ctx);
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), {
    resource: "https://api.doctorcre.com/doc/mcp",
    authorization_servers: ["https://api.doctorcre.com"],
    bearer_methods_supported: ["header"],
  });
  const unauthorized = await current.fetch(request("/doc/mcp?profile=full"), env(), ctx);
  assert.equal(unauthorized.status, 401);
  const challenge = unauthorized.headers.get("www-authenticate");
  assert.ok(challenge);
  assert.match(
    challenge,
    /resource_metadata="https:\/\/api\.doctorcre\.com\/\.well-known\/oauth-protected-resource\/doc\/mcp"/,
  );
});

test("existing OAuth metadata and /mcp missing-token behavior match the before implementation", async () => {
  // Captured from the unmodified origin/main entrypoint at 72bac3e9.
  const prior: unknown = JSON.parse(readFileSync(new URL("./doc-oauth-before.json", import.meta.url), "utf8"));
  assert.ok(typeof prior === "object" && prior !== null);
  for (const [path, a] of Object.entries(prior)) {
    assert.ok(typeof a === "object" && a !== null && "status" in a && "challenge" in a && "body" in a);
    const b = await current.fetch(request(path), env(), ctx);
    assert.equal(b.status, a.status, path);
    assert.equal(b.headers.get("www-authenticate"), a.challenge, path);
    assert.deepEqual(await b.json(), a.body, path);
  }
});
