// Exercise HTTP admission and dependency lifetime without network access.

import assert from "node:assert/strict";
import test from "node:test";
import { createPortalWebHandler, PORTAL_ORIGIN } from "../src/portal-web.ts";

type Options = Parameters<typeof createPortalWebHandler>[0];
function fixture(overrides: Partial<Options> = {}) {
  let time = Date.now();
  const sessions = new Set<string>();
  const host = createPortalWebHandler({
    now: () => time,
    signal: () => {},
    exchange: async (a) => {
      if (a.password === "lock") return { status: "locked", retry_at: new Date(time + 900000).toISOString() };
      if (a.password !== "existing") return { status: "denied" };
      sessions.add(a.slug + a.digest);
      return { status: "success", expires_at: new Date(time + 604800000).toISOString() };
    },
    read: async (a) =>
      sessions.has(a.slug + a.digest)
        ? {
            status: "success",
            schema_version: "client-portal.v1",
            portal: { slug: a.slug, roadmap: { phases: [] }, materials: [] },
          }
        : { status: "denied" },
    revoke: async (a) => {
      sessions.delete(a.slug + a.digest);
      return { status: "success" };
    },
    proposalRead: async () => {
      throw Error();
    },
    proposalSave: async () => {
      throw Error();
    },
    ...overrides,
  });
  const send = (path: string, method = "GET", cookie = "", body?: string, headers: Record<string, string> = {}) =>
    host.fetch(
      new Request(PORTAL_ORIGIN + path, {
        method,
        headers: { origin: PORTAL_ORIGIN, cookie, "content-type": "application/x-www-form-urlencoded", ...headers },
        ...(body === undefined ? {} : { body }),
      }),
    );
  const form = async (slug = "alpha") => {
    const r = await send(`/${slug}/`);
    assert.equal(r.status, 200);
    const csrf = /name="portal-csrf" content="([a-f0-9]+)"/.exec(await r.text())?.[1];
    assert.ok(csrf);
    return { csrf, cookie: String(r.headers.get("set-cookie")).split(";")[0] ?? "" };
  };
  const login = async (slug = "alpha") => {
    const f = await form(slug);
    const r = await send(`/${slug}/login`, "POST", f.cookie, `password=existing&csrf=${f.csrf}`);
    assert.equal(r.status, 303);
    const cookie = String(r.headers.get("set-cookie")).split(";")[0] ?? "";
    const status = await (await send(`/${slug}/status`, "GET", cookie)).json();
    return { cookie, csrf: String(status.csrf) };
  };
  return { host, send, form, login, advance: (ms: number) => (time += ms) };
}
test("login CSRF lockout and per-slug sessions", async () => {
  const f = fixture(),
    a = await f.form();
  assert.equal((await f.send("/alpha/login", "POST", a.cookie, "password=existing")).status, 403);
  assert.equal((await f.send("/alpha/login", "POST", a.cookie, `password=wrong&csrf=${a.csrf}`)).status, 401);
  const locked = await f.send("/alpha/login", "POST", a.cookie, `password=lock&csrf=${a.csrf}`);
  assert.equal(locked.status, 429);
  assert.equal(locked.headers.get("retry-after"), "900");
  const one = await f.login(),
    two = await f.login("beta"),
    jar = `${one.cookie}; ${two.cookie}`;
  assert.equal((await f.send("/alpha/status", "GET", jar)).status, 200);
  assert.equal((await f.send("/beta/status", "GET", jar)).status, 200);
  assert.equal((await f.send("/beta/status", "GET", one.cookie.replace("portal-alpha", "portal-beta"))).status, 401);
});
test("logout CSRF revocation repeat and clearing", async () => {
  const f = fixture(),
    a = await f.login();
  assert.equal((await f.send("/alpha/logout", "POST", a.cookie, "csrf=bad")).status, 403);
  for (let i = 0; i < 2; i++) {
    const r = await f.send("/alpha/logout", "POST", a.cookie, `csrf=${a.csrf}`);
    assert.equal(r.status, 303);
    assert.match(String(r.headers.get("set-cookie")), /Max-Age=0/);
  }
  assert.equal((await f.send("/alpha/status", "GET", a.cookie)).status, 401);
});
test("origin paths methods and body bounds", async () => {
  const f = fixture(),
    a = await f.form();
  assert.equal(
    (await f.send("/alpha/login", "POST", a.cookie, `csrf=${a.csrf}`, { origin: "https://evil.test" })).status,
    403,
  );
  assert.equal((await f.send("/alpha/login")).status, 405);
  for (const path of ["/alpha/?deal=x", "/alpha%2fbeta/", "/alpha/publish"])
    assert.ok((await f.send(path)).status >= 400);
  assert.equal((await f.send("/alpha/login", "POST", a.cookie, "x".repeat(8193))).status, 413);
  assert.equal(
    (await f.send("/alpha/login", "POST", a.cookie, "{}", { "content-type": "application/json" })).status,
    415,
  );
});
test("session rate limits isolate browsers and buckets evict", async () => {
  const f = fixture({ actionLimit: 3 }),
    a = await f.login(),
    b = await f.login();
  for (let i = 0; i < 2; i++) assert.equal((await f.send("/alpha/status", "GET", a.cookie)).status, 200);
  const limited = await f.send("/alpha/status", "GET", a.cookie);
  assert.equal(limited.status, 429);
  assert.deepEqual(await limited.json(), { error: "rate_limited" });
  assert.equal((await f.send("/alpha/status", "GET", b.cookie)).status, 200);
  const g = fixture({ maxRateBuckets: 1 });
  await g.send("/alpha/");
  assert.equal((await g.send("/beta/")).status, 503);
  g.advance(60000);
  assert.equal((await g.send("/beta/")).status, 200);
});
test("deadline retains slot until ignored cancellation settles", async () => {
  let release: (value: unknown) => void = () => {};
  let calls = 0;
  const f = fixture({
    deadlineMs: 15,
    maxConcurrent: 1,
    read: () => {
      calls++;
      return new Promise((r) => {
        release = r;
      });
    },
  });
  const cookie = `__Host-portal-alpha=${"a".repeat(64)}`;
  assert.equal((await f.send("/alpha/status", "GET", cookie)).status, 503);
  assert.equal((await f.send("/alpha/status", "GET", cookie)).status, 503);
  assert.equal(calls, 1);
  release({ status: "denied" });
});
test("breaker cooldown and live recovery", async () => {
  let calls = 0,
    fail = true;
  const f = fixture({
      read: async () => {
        calls++;
        if (fail) throw Error("private");
        return { status: "denied" };
      },
    }),
    cookie = `__Host-portal-alpha=${"a".repeat(64)}`;
  for (let i = 0; i < 4; i++) {
    const r = await f.send("/alpha/status", "GET", cookie);
    assert.equal(r.status, 503);
    assert.doesNotMatch(await r.text(), /private/);
  }
  assert.equal(calls, 3);
  f.advance(30000);
  fail = false;
  assert.equal((await f.send("/alpha/status", "GET", cookie)).status, 401);
});
test("bounded request admission and streamed body deadline", async () => {
  let release: (value: unknown) => void = () => {},
    entered: () => void = () => {};
  const started = new Promise<void>((r) => {
    entered = r;
  });
  const f = fixture({
    maxRequests: 1,
    read: () => {
      entered();
      return new Promise((r) => {
        release = r;
      });
    },
  });
  const cookie = `__Host-portal-alpha=${"a".repeat(64)}`;
  const pending = f.send("/alpha/status", "GET", cookie);
  await started;
  assert.equal((await f.send("/beta/")).status, 503);
  release({ status: "denied" });
  assert.equal((await pending).status, 401);
  const g = fixture({ deadlineMs: 15 });
  const body = new ReadableStream<Uint8Array>({ start() {} });
  const init = {
    method: "POST",
    headers: { origin: PORTAL_ORIGIN, "content-type": "application/x-www-form-urlencoded" },
    body,
    duplex: "half",
  };
  const request = new Request(`${PORTAL_ORIGIN}/alpha/login`, init);
  assert.equal((await g.host.fetch(request)).status, 408);
});
test("projection retains published items and escapes content with secure cookie bounds", async () => {
  const titles = [
    ["<script>bad</script>", "&lt;script&gt;bad&lt;/script&gt;"],
    ["<SCRIPT>bad</SCRIPT>", "&lt;SCRIPT&gt;bad&lt;/SCRIPT&gt;"],
    ['<script src="x">bad</script>', "&lt;script src=\\&quot;x\\&quot;&gt;bad&lt;/script&gt;"],
    ['" onmouseover="bad\'&', "\\&quot; onmouseover=\\&quot;bad&#39;&amp;"],
    ["a & b &amp; c", "a &amp; b &amp;amp; c"],
  ] as const;
  for (const [title, escapedTitle] of titles) {
    const f = fixture({
      read: async () => ({
        status: "success",
        schema_version: "client-portal.v1",
        portal: {
          slug: "alpha",
          roadmap: { phases: [] },
          materials: [],
          publications: [
            {
              id: "published",
              kind: "tour",
              title,
              content: "shared",
              internal_notes: "private",
            },
          ],
          internal_notes: "private",
        },
      }),
    });
    const a = await f.login();
    const page = await f.send("/alpha/", "GET", a.cookie);
    assert.equal(page.status, 200);
    const html = await page.text();
    assert.equal(
      html.split("<pre>")[1]?.split("</pre>")[0],
      `{
  &quot;slug&quot;: &quot;alpha&quot;,
  &quot;roadmap&quot;: {
    &quot;current_phase&quot;: null,
    &quot;phases&quot;: []
  },
  &quot;materials&quot;: [],
  &quot;publications&quot;: [
    {
      &quot;id&quot;: &quot;published&quot;,
      &quot;kind&quot;: &quot;tour&quot;,
      &quot;title&quot;: &quot;${escapedTitle}&quot;,
      &quot;content&quot;: &quot;shared&quot;
    }
  ]
}`,
      title,
    );
    assert.doesNotMatch(html, /private/);
    assert.match(String(page.headers.get("content-security-policy")), /frame-ancestors 'none'/);
    const form = await f.form();
    const login = await f.send("/alpha/login", "POST", form.cookie, `password=existing&csrf=${form.csrf}`);
    const cookie = String(login.headers.get("set-cookie"));
    assert.match(cookie, /Secure; HttpOnly; SameSite=Lax; Path=\//);
    assert.doesNotMatch(cookie, /Domain=/);
    for (const invalid of [`${a.cookie}; ${a.cookie}`, "__Host-portal-alpha=bad"])
      assert.equal((await fixture().send("/alpha/status", "GET", invalid)).status, 401);
  }
});
