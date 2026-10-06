import test from "node:test";
import assert from "node:assert/strict";
import { builtinModules } from "node:module";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

test("Worker entrypoint applies OAuth policy before authentication and alternate MCP doors", async () => {
  const repo = fileURLToPath(new URL("../../", import.meta.url));
  const bundle = await build({ absWorkingDir: repo, entryPoints: ["mcp-server/src/index.js"],
    bundle: true, write: false, format: "esm", platform: "neutral", logLevel: "silent",
    mainFields: ["browser", "module", "main"], conditions: ["workerd", "worker", "browser"],
    external: ["cloudflare:*", "node:*"], loader: { ".ttf": "binary" },
    banner: { js: 'import { createRequire } from "node:module"; const require = createRequire("/index.js");' },
    plugins: [{ name: "node-compat", setup(builder) {
      builder.onResolve({ filter: /^[a-z]/ }, args => builtinModules.includes(args.path)
        ? { path: "node:" + args.path, external: true } : undefined);
    } }],
  });
  // Explicit synthetic bindings; no wrangler config, local credential file,
  // Google endpoint or production database is read by this fixture.
  const worker = new Miniflare({ ...convertV4MiniflareOptions({ modules: true, script: bundle.outputFiles[0].text,
    compatibilityDate: "2026-09-01", compatibilityFlags: ["nodejs_compat", "global_fetch_strictly_public"],
    kvNamespaces: ["OAUTH_KV"], bindings: { GOOGLE_CLIENT_ID: "synthetic-config", GOOGLE_CLIENT_SECRET: "synthetic-config" },
    durableObjects: { OAUTH_CONSENT_STATE: { className: "OAuthConsentState", useSQLite: true } },
  }), resourcePersistencePath: fileURLToPath(new URL("../../out/_to_delete/oauth-worker-fixture/", import.meta.url)),
    unsafeEnableSharedStorage: false });
  try {
    const origin = "https://oauth.example";
    for (const path of ["/mcp", "/doc/mcp", "/pipeline/changes"]) {
      for (const method of ["GET", "POST", "OPTIONS"]) {
        const refused = await worker.dispatchFetch(origin + path, { method, headers: { origin: "null" } });
        assert.equal(refused.status, 403);
        assert.equal(refused.headers.get("access-control-allow-origin"), null);
      }
    }
    assert.equal((await worker.dispatchFetch(origin + "/healthz")).status, 200);
    for (const allowed of [null, "https://chatgpt.com", "https://claude.ai"]) {
      assert.equal((await worker.dispatchFetch(origin + "/mcp", { headers: allowed ? { origin: allowed } : {} })).status, 401);
    }
    assert.equal((await worker.dispatchFetch(origin + "/consent")).status, 405);
    assert.equal((await worker.dispatchFetch(origin + "/token", { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" }, body: "grant_type=authorization_code" })).status, 400);
  } finally { await worker.dispose(); }
});

test("Workerd SQLite consent consumption is atomic, persistent and deadline bound", async () => {
  const source = await readFile(new URL("../src/oauth-consent-state.js", import.meta.url), "utf8");
  const worker = new Miniflare({ ...convertV4MiniflareOptions({ modules: true,
    script: source + '\nexport default { fetch(request, env) { return env.OAUTH_CONSENT_STATE.get(env.OAUTH_CONSENT_STATE.idFromName(new URL(request.url).searchParams.get("id"))).fetch(request); } };',
    compatibilityDate: "2026-09-01",
    durableObjects: { OAUTH_CONSENT_STATE: { className: "OAuthConsentState", useSQLite: true } },
  }), resourcePersistencePath: fileURLToPath(new URL("../../out/_to_delete/oauth-consent-fixture/", import.meta.url)), unsafeEnableSharedStorage: false });
  const id = crypto.randomUUID();
  const call = (operation, input, consentId = id) => worker.dispatchFetch(`https://fixture.example/${operation}?id=${consentId}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(input),
  });
  try {
    const pending = { state: "synthetic-state", csrf: "synthetic-csrf", browser: "synthetic-browser-hash", encryptedGrant: "synthetic-ciphertext", expiresAt: Date.now() + 600000 };
    assert.equal((await call("create", pending)).status, 204);
    assert.equal((await call("consume", { ...pending, csrf: "wrong" })).status, 403);
    const responses = await Promise.all(Array.from({ length: 8 }, () => call("consume", pending)));
    assert.equal(responses.filter(r => r.status === 200).length, 1);
    assert.equal(responses.filter(r => r.status === 400).length, 7);
    assert.equal((await call("consume", pending)).status, 400);
    const expiredId = crypto.randomUUID();
    assert.equal((await call("create", { ...pending, expiresAt: Date.now() - 1 }, expiredId)).status, 204);
    assert.equal((await call("consume", pending, expiredId)).status, 400);
  } finally { await worker.dispose(); }
});
