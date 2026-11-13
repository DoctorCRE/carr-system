import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { discoverSuites } from "../suite-discovery.ts";

test("discovery enforces its checked-in floor and diagnoses renamed or nested suites", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "suite-discovery-"));
  try {
    await Promise.all(Array.from({ length: 407 }, (_, n) => writeFile(path.join(directory, `${n}.test.mjs`), "")));
    assert.equal((await discoverSuites(directory, { expected: 407 })).length, 407);
    await rm(path.join(directory, "0.test.mjs"));
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /found 406, expected at least 407; drop 1/);
    await mkdir(path.join(directory, "nested"));
    await rename(path.join(directory, "1.test.mjs"), path.join(directory, "nested/1.test.mjs"));
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /omit nested\/1.test.mjs/);
    await rm(path.join(directory, "nested"), { recursive: true });
    await writeFile(path.join(directory, "renamed.mjs"), "import test from 'node:test'; test('lost', () => {});");
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /omit renamed.mjs/);
    await rm(path.join(directory, "renamed.mjs"));
    await writeFile(path.join(directory, "unsupported.test.cts"), "");
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /omit unsupported.test.cts/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("the checked-in floor holds at this head and adding a suite needs no edit", async () => {
  const inventory: unknown = JSON.parse(await readFile(new URL("../suite-inventory.json", import.meta.url), "utf8"));
  assert.ok(
    typeof inventory === "object" &&
      inventory !== null &&
      "expected" in inventory &&
      typeof inventory.expected === "number",
  );
  const suites = (await readdir(new URL("./", import.meta.url))).filter((name) => /\.test\.(?:js|mjs|ts)$/.test(name));
  assert.ok(inventory.expected <= suites.length, "the checked-in floor must not exceed this head");
  const directory = await mkdtemp(path.join(tmpdir(), "real-suite-inventory-"));
  try {
    await Promise.all(suites.map((name) => writeFile(path.join(directory, name), "")));
    assert.equal((await discoverSuites(directory, inventory)).length, suites.length);
    await writeFile(path.join(directory, "extra.test.mjs"), "");
    assert.equal((await discoverSuites(directory, inventory)).length, suites.length + 1);
    await rm(path.join(directory, "extra.test.mjs"));
    const floor = { ...inventory, expected: suites.length };
    for (const name of suites) {
      await rename(path.join(directory, name), path.join(directory, `${name}.disabled`));
      await assert.rejects(
        discoverSuites(directory, floor),
        { message: new RegExp(`found ${suites.length - 1}, expected at least ${suites.length}; drop 1`) },
        name,
      );
      await rename(path.join(directory, `${name}.disabled`), path.join(directory, name));
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("mixed root JavaScript and TypeScript suites are discovered together", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "mixed-suites-"));
  try {
    for (const name of ["a.test.ts", "b.test.js", "c.test.mjs"]) await writeFile(path.join(directory, name), "");
    assert.deepEqual(await discoverSuites(directory, { expected: 3 }), ["a.test.ts", "b.test.js", "c.test.mjs"]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("inventory JSON is validated before traversal", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "invalid-inventory-"));
  try {
    for (const inventory of [
      null,
      [],
      1,
      {},
      { expected: 0 },
      { expected: 1.5 },
      { expected: "1" },
      { expected: 1, external: null },
      { expected: 1, external: "x" },
      { expected: 1, external: [1] },
    ]) {
      await assert.rejects(discoverSuites(directory, inventory), /Invalid checked-in unit/);
    }
    await assert.rejects(discoverSuites(directory, { expected: 1 }), /found 0, expected at least 1/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("external exceptions remain excluded while nested and renamed TypeScript suites are refused", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "external-suites-"));
  try {
    await writeFile(path.join(directory, "unit.test.ts"), "");
    await writeFile(
      path.join(directory, "acceptance.postgres.mjs"),
      "import test from 'node:test'; test('external', () => {});",
    );
    const inventory = { expected: 1, external: ["acceptance.postgres.mjs"] };
    assert.deepEqual(await discoverSuites(directory, inventory), ["unit.test.ts"]);
    await mkdir(path.join(directory, "nested"));
    await rename(path.join(directory, "unit.test.ts"), path.join(directory, "nested/unit.test.ts"));
    await assert.rejects(discoverSuites(directory, inventory), /omit nested\/unit.test.ts/);
    await rm(path.join(directory, "nested"), { recursive: true });
    await writeFile(path.join(directory, "renamed.ts"), "import test from 'node:test'; test('lost', () => {});");
    await assert.rejects(discoverSuites(directory, inventory), /omit renamed.ts/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("typed suites retain serialized browser and PostgreSQL batches", () => {
  const result = spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      `
    import { suiteBatches } from './mcp-server/run-tests.ts';
    console.log(JSON.stringify(suiteBatches([
      'ordinary.test.ts', 'chrome-launch.test.mjs', 'auto-browser.test.ts',
      'source-browser.test.ts', 'auto-postgres.test.ts', 'source-pg.test.ts', 'legacy.test.js'
    ], ['source-browser.test.ts'], ['source-pg.test.ts'])));
  `,
    ],
    { cwd: new URL("../../", import.meta.url), encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  const batches: unknown = JSON.parse(result.stdout);
  assert.deepEqual(batches, [
    ["chrome-launch.test.mjs"],
    ["auto-browser.test.ts"],
    ["source-browser.test.ts"],
    ["auto-postgres.test.ts"],
    ["source-pg.test.ts"],
    ["legacy.test.js", "ordinary.test.ts"],
  ]);
});

test("canonical runner executes mixed suites exactly once and validates shard selections", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "typed-runner-"));
  try {
    await mkdir(path.join(directory, "test"));
    for (const file of ["run-tests.ts", "suite-discovery.ts"]) {
      await copyFile(new URL(`../${file}`, import.meta.url), path.join(directory, file));
    }
    await writeFile(path.join(directory, "package.json"), JSON.stringify({ type: "module" }));
    await writeFile(path.join(directory, "suite-inventory.json"), JSON.stringify({ expected: 3 }));
    const files = ["a.test.ts", "b.test.js", "c.test.mjs"];
    for (const file of files) {
      await writeFile(path.join(directory, "test", file), `import test from 'node:test'; test('${file}', () => {});`);
    }
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    const run = (args: string[], selected: string[]) =>
      spawnSync(process.execPath, args, {
        cwd: directory,
        encoding: "utf8",
        env: { ...env, CARR_CI_UNIT_SUITES: JSON.stringify(selected) },
      });
    const direct = run(["--test", ...files.map((file) => `test/${file}`)], files);
    assert.equal(direct.status, 0, direct.stderr);
    assert.match(direct.stdout, /(?:#|ℹ) tests 3/);
    const canonical = run(["run-tests.ts"], files);
    assert.equal(canonical.status, 0, canonical.stderr);
    const receipts = canonical.stdout
      .split("\n")
      .filter((line) => line.startsWith("suite-result: "))
      .map((line) => {
        const receipt: unknown = JSON.parse(line.slice("suite-result: ".length));
        return receipt;
      });
    assert.equal(receipts.length, 3);
    for (const file of files)
      assert.ok(
        receipts.some(
          (receipt) =>
            typeof receipt === "object" &&
            receipt !== null &&
            "file" in receipt &&
            receipt.file === file &&
            "code" in receipt &&
            receipt.code === 0 &&
            "summary" in receipt &&
            typeof receipt.summary === "string" &&
            /(?:#|ℹ) tests 1/.test(receipt.summary),
        ),
      );
    const shard = run(["run-tests.ts"], ["a.test.ts"]);
    assert.equal(shard.status, 0, shard.stderr);
    assert.equal(shard.stdout.split("\n").filter((line) => line.startsWith("suite-result: ")).length, 1);
    assert.match(shard.stdout, /"file":"a.test.ts"/);
    for (const selection of [[], ["a.test.ts", "a.test.ts"], ["missing.test.ts"]]) {
      const invalid = run(["run-tests.ts"], selection);
      assert.notEqual(invalid.status, 0);
      assert.match(invalid.stderr, /unit shard has empty, duplicate or missing suites/);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("PostgreSQL capacity refusal stops before snapshot acquisition without retries", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "capacity-runner-"));
  try {
    await mkdir(path.join(directory, "test"));
    for (const file of ["run-tests.ts", "suite-discovery.ts"]) {
      await copyFile(new URL(`../${file}`, import.meta.url), path.join(directory, file));
    }
    await writeFile(path.join(directory, "package.json"), JSON.stringify({ type: "module" }));
    await writeFile(path.join(directory, "suite-inventory.json"), JSON.stringify({ expected: 1 }));
    await writeFile(
      path.join(directory, "test", "capacity-postgres.test.ts"),
      "throw new Error('suite must not start');",
    );
    const probe = path.join(directory, "probe");
    await writeFile(
      probe,
      "#!/usr/bin/env node\nprocess.stdout.write('injected capacity unreadable'); process.exit(78);\n",
      { mode: 0o700 },
    );
    const env: NodeJS.ProcessEnv = { ...process.env, CARR_CI_PYTHON: probe };
    delete env.NODE_TEST_CONTEXT;
    const result = spawnSync(process.execPath, ["run-tests.ts"], { cwd: directory, env, encoding: "utf8" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /PostgreSQL capacity preflight refused: injected capacity unreadable/);
    assert.doesNotMatch(result.stderr, /Cannot find module|suite must not start/);
    assert.equal(result.stderr.match(/injected capacity unreadable/g)?.length, 1);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
