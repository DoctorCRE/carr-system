import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { copyFile, mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { reconcileRuntimeErrors } from "../../ops/runtime-error-watch.mts";

test("an incomplete incident census never starts a finding write", async () => {
  for (const page of [
    { ok: true, coverage: [{ kind: "incident", state: "unavailable", count_total: 0 }], items: [] },
    { ok: true, coverage: [{ kind: "incident", state: "complete", count_total: 1 }], items: [] },
    { ok: false, coverage: [{ kind: "incident", state: "complete", count_total: 0 }], items: [] },
  ]) {
    const calls: string[] = [];
    await assert.rejects(
      () =>
        reconcileRuntimeErrors(async (verb) => {
          calls.push(verb);
          return page;
        }),
      /runtime_incident_census/,
    );
    assert.deepEqual(calls, ["unfinished-work"]);
  }
});

test("source details and malformed identities cannot enter runtime findings", async () => {
  const ref = "INC-20260101-01";
  for (const incident of [
    { ref, source_ref: "elsewhere", environment: "local" },
    {
      ref,
      source_ref: "mcp-server/src/trace.js",
      detected_source: "carr-mcp-worker",
      environment: "secret@example.invalid",
      fingerprint: "carr-mcp|secret@example.invalid|/route|http_5xx",
    },
    {
      ref,
      source_ref: "mcp-server/src/trace.js",
      detected_source: "carr-mcp-worker",
      environment: "local",
      fingerprint: "carr-mcp|local|/route|private-error-value",
    },
  ]) {
    const calls: string[] = [];
    const result = await reconcileRuntimeErrors(async (verb) => {
      calls.push(verb);
      return verb === "unfinished-work"
        ? {
            ok: true,
            coverage: [{ kind: "incident", state: "complete", count_total: 1 }],
            items: [{ id: ref, kind: "incident" }],
          }
        : { incident, links: [] };
    });
    assert.equal(result.linked, 0);
    assert.deepEqual(calls, ["unfinished-work", "get-incident"]);
  }
});

test("all incident pages are read before writes, and a repeated cursor refuses", async () => {
  const ref = "INC-20260101-01";
  const calls: [string, Record<string, unknown>][] = [];
  await reconcileRuntimeErrors(async (verb, args) => {
    calls.push([verb, args]);
    if (verb === "get-incident") return { incident: { source_ref: "unrelated" }, links: [] };
    return {
      ok: true,
      coverage: [{ kind: "incident", state: "complete", count_total: 1 }],
      items: args.cursor ? [{ id: ref, kind: "incident" }] : [],
      next_cursor: args.cursor ? null : "next",
    };
  });
  assert.deepEqual(
    calls.map(([verb]) => verb),
    ["unfinished-work", "unfinished-work", "get-incident"],
  );
  assert.equal(calls[1]?.[1].cursor, "next");
  await assert.rejects(
    () =>
      reconcileRuntimeErrors(async () => ({
        ok: true,
        coverage: [{ kind: "incident", state: "complete", count_total: 0 }],
        items: [],
        next_cursor: "same",
      })),
    /runtime_incident_cursor_repeated/,
  );
});

test("a 1,501-incident backlog bounds card reads and keeps progress through existing links", async (t) => {
  for (const failAt of [null, 8]) {
    await t.test(
      failAt ? "a later card failure preserves earlier links and resumes" : "stop after ten groups",
      async (t) => {
        const rows = Array.from({ length: 1501 }, (_, i) => ({
          id: `INC-20260101-${String(i + 1).padStart(4, "0")}`,
          kind: "incident",
        }));
        const links = new Set<unknown>();
        let reads = 0,
          failed = false;
        const call: Parameters<typeof reconcileRuntimeErrors>[0] = async (verb, args) => {
          if (verb === "unfinished-work") {
            const offset = Number(args.cursor ?? 0);
            return {
              ok: true,
              coverage: [{ kind: "incident", state: "complete", count_total: rows.length }],
              items: rows.slice(offset, offset + 500),
              next_cursor: offset + 500 < rows.length ? String(offset + 500) : null,
            };
          }
          if (verb === "get-incident") {
            reads += 1;
            if (reads === failAt && !failed) {
              failed = true;
              throw new Error("synthetic late card failure");
            }
            return {
              incident: {
                ref: args.ref,
                source_ref: "mcp-server/src/trace.js",
                detected_source: "carr-mcp-worker",
                environment: "local",
                state: "detected",
                fingerprint: "carr-mcp|local|/synthetic|http_5xx",
              },
              links: links.has(args.ref) ? [{ kind: "work_request" }] : [],
            };
          }
          if (verb === "record-finding") return { flag_id: "10000000-0000-4000-8000-000000000001" };
          if (verb === "report-problem") return { human_ref: "WR-1" };
          assert.equal(verb, "link-incident-work-request");
          links.add(args.incident_ref);
          return { ok: true, incident_ref: args.incident_ref, work_request: args.work_request };
        };
        if (failAt) {
          await assert.rejects(() => reconcileRuntimeErrors(call), /synthetic late card failure/);
          t.diagnostic(`${reads} card reads, ${links.size} completed groups before failure`);
          assert.deepEqual(
            [...links],
            rows.slice(0, 7).map((row) => row.id),
          );
          reads = 0;
        }
        const result = await reconcileRuntimeErrors(call);
        t.diagnostic(`${reads} card reads, ${result.linked} new groups, ${links.size} total linked`);
        assert.equal(result.linked, 10);
        assert.equal(reads, failAt ? 17 : 10);
        assert.deepEqual(
          [...links],
          rows.slice(0, failAt ? 17 : 10).map((row) => row.id),
        );
      },
    );
  }
});

test("malformed census JSON refuses before any write", async () => {
  for (const page of [
    null,
    [],
    "private-value",
    { ok: true, coverage: [null], items: [] },
    { ok: true, coverage: [{ kind: "incident", state: "complete", count_total: 0 }], items: [], next_cursor: 9 },
  ]) {
    const calls: string[] = [];
    await assert.rejects(
      () =>
        reconcileRuntimeErrors(async (verb) => {
          calls.push(verb);
          return page;
        }),
      /runtime_incident_census_incomplete/,
    );
    assert.deepEqual(calls, ["unfinished-work"]);
  }
});

test("changed totals, duplicate identities and invalid references refuse the census", async () => {
  let page = 0;
  await assert.rejects(
    () =>
      reconcileRuntimeErrors(async () => ({
        ok: true,
        coverage: [{ kind: "incident", state: "complete", count_total: page++ }],
        items: [],
        next_cursor: "next",
      })),
    /runtime_incident_census_changed/,
  );
  for (const rows of [
    [
      { id: "INC-20260101-01", kind: "incident" },
      { id: "INC-20260101-01", kind: "incident" },
    ],
    [{ id: "private-value", kind: "incident" }],
  ]) {
    await assert.rejects(
      () =>
        reconcileRuntimeErrors(async () => ({
          ok: true,
          coverage: [{ kind: "incident", state: "complete", count_total: rows.length }],
          items: rows,
        })),
      /runtime_incident_(census_short|ref_invalid)/,
    );
  }
});

test("CLI prints an empty reconciliation and redacts a refused record reply", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "runtime-error-watch-")));
  try {
    await mkdir(join(root, "ops"));
    const script = join(root, "ops/runtime-error-watch.mts");
    await copyFile(new URL("../../ops/runtime-error-watch.mts", import.meta.url), script);
    await writeFile(
      join(root, "run.sh"),
      '#!/usr/bin/env python3\nimport os\nprint(os.environ["RUNTIME_TEST_REPLY"])\n',
      { mode: 0o700 },
    );
    const run = (reply: unknown) =>
      promisify(execFile)(process.execPath, [script], {
        env: { ...process.env, RUNTIME_TEST_REPLY: JSON.stringify(reply) },
        timeout: 5000,
      });
    const success = await run({
      ok: true,
      coverage: [{ kind: "incident", state: "complete", count_total: 0 }],
      items: [],
    });
    assert.equal(success.stdout.trim(), '{"linked":0}');
    assert.equal(success.stderr, "");
    await assert.rejects(
      () => run({ ok: false, error: "synthetic-private-detail" }),
      (error: unknown) => {
        assert.ok(error instanceof Error && "code" in error && "stdout" in error && "stderr" in error);
        assert.equal(error.code, 1);
        assert.equal(error.stdout, "");
        assert.equal(error.stderr, "runtime-error-watch: reconciliation failed; durable incidents remain pending\n");
        return true;
      },
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
