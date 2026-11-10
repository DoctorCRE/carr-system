import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { migrationIssues, object } from "./migration.ts";

const source = resolve(import.meta.dirname, "..");
const fixtureEnv = Object.fromEntries(
  Object.entries(process.env).filter(([name]) => !name.startsWith("GIT_") && !name.startsWith("GITHUB_")),
);

test("staged gate accepts clean code and refuses each negative seed", () => {
  const root = mkdtempSync(join(tmpdir(), "carr-typescript-"));
  try {
    const pkg = join(root, "mcp-server");
    cpSync(join(source, "typescript"), join(pkg, "typescript"), { recursive: true });
    for (const file of [
      "package.json",
      "tsconfig.base.json",
      "tsconfig.node.json",
      "tsconfig.worker.json",
      "biome.json",
      "knip.json",
    ]) {
      cpSync(join(source, file), join(pkg, file));
    }
    const packagePath = join(pkg, "package.json");
    const packageConfig = object(packagePath);
    writeFileSync(
      packagePath,
      JSON.stringify({ ...packageConfig, scripts: { "check:staged": "node typescript/check.ts" } }),
    );
    const fixtureFiles = ["typescript/check.ts", "typescript/migration.ts", "typescript/gate.test.ts"];
    for (const [name, files] of [
      ["tsconfig.node.json", fixtureFiles],
      ["tsconfig.worker.json", []],
    ] satisfies [string, string[]][]) {
      const config = object(join(pkg, name));
      writeFileSync(join(pkg, name), JSON.stringify({ ...config, files }));
    }
    writeFileSync(
      join(pkg, "knip.json"),
      JSON.stringify({ entry: ["typescript/*.test.ts"], project: [...fixtureFiles].sort() }),
    );
    symlinkSync(join(source, "node_modules"), join(pkg, "node_modules"), "dir");
    writeFileSync(
      join(pkg, "typescript/migration.json"),
      JSON.stringify({ legacyJavaScript: [], legacyTypeScript: [] }),
    );
    execFileSync("git", ["init", "-q", root], { env: fixtureEnv });
    writeFileSync(join(root, ".gitignore"), "node_modules/\n");
    const run = () =>
      spawnSync("npm", ["--prefix", pkg, "run", "check:staged"], {
        cwd: root,
        env: fixtureEnv,
        encoding: "utf8",
        timeout: 120_000,
      });
    const refusal = (result: ReturnType<typeof run>, rule: RegExp) => {
      const output = result.stdout + result.stderr;
      assert.equal(result.status, 1, output);
      assert.match(output, rule);
      assert.match(output, /Remedy: [^\n]+\n?$/);
      assert.doesNotMatch(output, /AssertionError|SyntaxError|\n\s+at /);
    };
    const scopePath = join(pkg, "typescript/migration.json");
    const legacySource = "export const legacy = 1;\n";
    const baseline = {
      legacyJavaScript: ["mcp-server/legacy.js"],
      legacyTypeScript: ["mcp-server/legacy.ts"],
    };
    writeFileSync(scopePath, JSON.stringify(baseline));
    const git = (...args: string[]) => execFileSync("git", args, { cwd: root, env: fixtureEnv });
    writeFileSync(join(pkg, "legacy.ts"), legacySource);
    writeFileSync(join(pkg, "legacy.js"), legacySource);
    git("add", "mcp-server/typescript/migration.json", "mcp-server/legacy.ts", "mcp-server/legacy.js");
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "baseline");
    git("update-ref", "refs/remotes/origin/main", "HEAD");
    writeFileSync(join(pkg, "new.js"), "export const value = 1;\n");
    writeFileSync(scopePath, JSON.stringify({ ...baseline, toolExceptions: ["mcp-server/new.js"] }));
    refusal(run(), /toolExceptions|New first-party JavaScript/);
    rmSync(join(pkg, "new.js"));
    writeFileSync(scopePath, "{");
    refusal(run(), /migration.json.*valid JSON/);
    writeFileSync(scopePath, JSON.stringify(baseline));
    const migrationPath = join(pkg, "typescript/migration.ts");
    const originalMigration = readFileSync(migrationPath, "utf8");
    writeFileSync(
      migrationPath,
      originalMigration.replace(
        'const root = resolve(pkg, "..");',
        'throw new TypeError("internal bug");\n  const root = resolve(pkg, "..");',
      ),
    );
    const internal = run();
    writeFileSync(migrationPath, originalMigration);
    const internalOutput = internal.stdout + internal.stderr;
    assert.equal(internal.status, 1, internalOutput);
    assert.match(internalOutput, /TypeError: internal bug/);
    assert.match(internalOutput, /\n\s+at migrationIssues .*migration\.ts:/);
    assert.doesNotMatch(internalOutput, /Remedy:/);
    const checkerPath = join(pkg, "typescript/check.ts");
    const originalChecker = readFileSync(checkerPath, "utf8");
    writeFileSync(
      checkerPath,
      originalChecker.replace("syntax = parse(", 'throw new TypeError("parser boundary bug");\n      syntax = parse('),
    );
    const parserBug = run();
    writeFileSync(checkerPath, originalChecker);
    const parserOutput = parserBug.stdout + parserBug.stderr;
    assert.equal(parserBug.status, 1, parserOutput);
    assert.match(parserOutput, /TypeError: parser boundary bug/);
    assert.match(parserOutput, /\n\s+at main .*check\.ts:/);
    assert.doesNotMatch(parserOutput, /Remedy:/);
    const inputRefusals: [string, string | null, RegExp][] = [
      ["typescript/migration.json", "[]", /migration.json.*JSON object/],
      ["typescript/migration.json", null, /migration.json.*readable valid JSON/],
      ["typescript/migration.json", '{"legacyJavaScript":null,"legacyTypeScript":[]}', /legacyJavaScript.*array/],
      ["knip.json", '{"project":[]}', /knip.json.*cover every admitted file/],
      ["knip.json", '{"workspaces":{".":{"project":[]}}}', /knip.json.*cover every admitted file/],
      ["tsconfig.node.json", "{", /tsconfig.node.json.*readable valid JSON/],
      ["tsconfig.base.json", null, /tsconfig.base.json.*configuration must resolve/],
    ];
    for (const [path, content, rule] of inputRefusals) {
      const file = join(pkg, path);
      const original = readFileSync(file, "utf8");
      if (content === null) rmSync(file);
      else writeFileSync(file, content);
      refusal(run(), rule);
      writeFileSync(file, original);
    }
    const baselineFailures: string[] = [];
    for (const extension of ["js", "ts"]) {
      const path = `mcp-server/new.${extension}`;
      writeFileSync(join(root, path), "export const invalid: string = 1;\n");
      const scope = structuredClone(baseline);
      if (extension === "js") scope.legacyJavaScript.push(path);
      else scope.legacyTypeScript.push(path);
      writeFileSync(scopePath, JSON.stringify(scope));
      const result = run();
      if (result.status === 0) baselineFailures.push(`appended legacy .${extension} accepted`);
      else assert.match(result.stdout + result.stderr, /legacy.*only shrink/i);
      rmSync(join(root, path));
    }
    writeFileSync(join(pkg, "legacy.js"), "export const legacy = 1;\n");
    renameSync(join(pkg, "legacy.js"), join(pkg, "renamed.js"));
    writeFileSync(scopePath, JSON.stringify({ ...baseline, legacyJavaScript: ["mcp-server/renamed.js"] }));
    const renamed = run();
    if (renamed.status === 0) baselineFailures.push("renamed legacy file accepted");
    else assert.match(renamed.stdout + renamed.stderr, /legacy.*only shrink/i);
    rmSync(join(pkg, "renamed.js"));
    writeFileSync(scopePath, JSON.stringify({ legacyJavaScript: [], legacyTypeScript: [] }));
    rmSync(join(pkg, "legacy.ts"));
    const shrunk = run();
    assert.equal(shrunk.status, 0, shrunk.stdout + shrunk.stderr);
    assert.deepEqual(baselineFailures, [], baselineFailures.join("\n"));
    const baselineHead = git("rev-parse", "HEAD").toString().trim();
    git("update-ref", "-d", "refs/remotes/origin/main");
    const unavailable = run();
    refusal(unavailable, /refs\/remotes\/origin\/main/);
    assert.match(unavailable.stdout + unavailable.stderr, /Cannot read legacy migration baseline/);
    git("branch", "-M", "main");
    git("remote", "add", "origin", root);
    refusal(run(), /Cannot read legacy migration baseline/);
    assert.notEqual(
      spawnSync("git", ["rev-parse", "--verify", "refs/remotes/origin/main"], { cwd: root, env: fixtureEnv }).status,
      0,
    );
    git("update-ref", "refs/remotes/origin/main", baselineHead);
    const emptyTree = execFileSync("git", ["mktree"], { cwd: root, env: fixtureEnv, input: "" }).toString().trim();
    const initial = git(
      "-c",
      "user.name=Fixture",
      "-c",
      "user.email=fixture@example.invalid",
      "commit-tree",
      emptyTree,
      "-m",
      "before gate",
    )
      .toString()
      .trim();
    git("update-ref", "refs/remotes/origin/main", initial);
    const bootstrap = run();
    refusal(bootstrap, /Cannot read legacy migration baseline/);
    git("update-ref", "refs/remotes/origin/main", baselineHead);
    const clean = run();
    assert.equal(clean.status, 0, clean.stdout + clean.stderr);
    const cases: [string, string, RegExp][] = [
      ["mcp-server/typescript/broken.ts", "export const = ;\n", /typescript\/broken\.ts:1:14/],
      ["outside.js", "export const value = 1;\n", /new first-party JavaScript/i],
      ["mcp-server/new.js", "export const value = 1;\n", /new first-party JavaScript/i],
      ["mcp-server/typescript/seed.ts", "export const value: any = 1;\n", /any/i],
      ["mcp-server/typescript/seed.ts", "// @ts-ignore\nexport const value = 1;\n", /suppression/i],
      ["mcp-server/typescript/seed.ts", "export const value = [1][0]!;\n", /non-null/i],
      ["mcp-server/typescript/seed.ts", "export const value = 1\n", /biome|format/i],
      [
        "mcp-server/typescript/seed.ts",
        "export const usedSeed = 1;\nexport const unusedSeed = 2;\n",
        /Unused exports/i,
      ],
      ["mcp-server/typescript/seed.ts", "export const value: string = 1;\n", /TS2322/],
      ["mcp-server/unlisted.ts", "export {};\n", /strict project/i],
    ];
    for (const [path, text, expected] of cases) {
      assert.ok(path && text && expected instanceof RegExp);
      const nodeConfig = join(pkg, "tsconfig.node.json");
      const original = readFileSync(nodeConfig, "utf8");
      const graphPath = join(pkg, "knip.json");
      const originalGraph = readFileSync(graphPath, "utf8");
      if (path.endsWith("seed.ts") || path.endsWith("broken.ts")) {
        const config: unknown = JSON.parse(original);
        assert.ok(typeof config === "object" && config !== null && "files" in config && Array.isArray(config.files));
        config.files.push(path.replace("mcp-server/", ""));
        const graph: unknown = JSON.parse(originalGraph);
        assert.ok(typeof graph === "object" && graph !== null && "project" in graph && Array.isArray(graph.project));
        graph.project.push(path.replace("mcp-server/", ""));
        graph.project.sort();
        writeFileSync(graphPath, JSON.stringify(graph));
        writeFileSync(nodeConfig, JSON.stringify(config));
      }
      const file = join(root, path);
      const testPath = join(pkg, "typescript/gate.test.ts");
      const originalTest = readFileSync(testPath, "utf8");
      if (text.includes("unusedSeed")) {
        writeFileSync(
          testPath,
          originalTest
            .replace(
              'import { migrationIssues, object } from "./migration.ts";',
              'import { migrationIssues, object } from "./migration.ts";\nimport { usedSeed } from "./seed.ts";',
            )
            .replace("const source =", "assert.equal(usedSeed, 1);\n\nconst source ="),
        );
      }
      writeFileSync(file, text);
      const result = run();
      rmSync(file);
      writeFileSync(nodeConfig, original);
      writeFileSync(graphPath, originalGraph);
      writeFileSync(testPath, originalTest);
      refusal(result, expected);
    }
    const legacyFile = join(pkg, "legacy.ts");
    writeFileSync(legacyFile, legacySource);
    writeFileSync(
      join(pkg, "typescript/migration.json"),
      JSON.stringify({
        legacyJavaScript: [],
        legacyTypeScript: ["mcp-server/legacy.ts"],
      }),
    );
    const unchanged = run();
    assert.equal(unchanged.status, 0, unchanged.stdout + unchanged.stderr);
    writeFileSync(legacyFile, `${legacySource}export const changed = 2;\n`);
    const changed = run();
    assert.notEqual(changed.status, 0);
    assert.match(changed.stdout + changed.stderr, /strict project/);
    const rehashed = structuredClone(baseline);
    Object.assign(rehashed, {
      legacyTypeScript: {
        "mcp-server/legacy.ts": { sha256: createHash("sha256").update(readFileSync(legacyFile)).digest("hex") },
      },
    });
    writeFileSync(scopePath, JSON.stringify(rehashed));
    const rehashAttack = run();
    refusal(rehashAttack, /migration.json.*legacyTypeScript/);

    rmSync(legacyFile);
    writeFileSync(scopePath, JSON.stringify(baseline));
    const failures: string[] = [];
    for (const path of ["tsconfig.base.json", "tsconfig.node.json", "tsconfig.worker.json"]) {
      const configPath = join(pkg, path);
      const original = readFileSync(configPath, "utf8");
      const overrides: [string, boolean][] = [
        ["strict", false],
        ["noImplicitAny", false],
        ["strictNullChecks", false],
        ["strictFunctionTypes", false],
        ["strictBindCallApply", false],
        ["strictPropertyInitialization", false],
        ["noImplicitThis", false],
        ["alwaysStrict", false],
        ["useUnknownInCatchVariables", false],
        ["strictBuiltinIteratorReturn", false],
        ["noUncheckedIndexedAccess", false],
        ["noUnusedLocals", false],
        ["noUnusedParameters", false],
        ["exactOptionalPropertyTypes", false],
        ["noEmit", false],
        ["allowImportingTsExtensions", false],
        ["erasableSyntaxOnly", false],
        ["verbatimModuleSyntax", false],
        ["noCheck", true],
        ["skipLibCheck", true],
        ["skipDefaultLibCheck", true],
        ["allowUnreachableCode", true],
        ["allowUnusedLabels", true],
      ];
      for (const [flag, value] of overrides) {
        const config = object(configPath);
        config.compilerOptions ??= {};
        assert.ok(typeof config.compilerOptions === "object" && config.compilerOptions !== null);
        config.compilerOptions = { ...config.compilerOptions, [flag]: value };
        writeFileSync(configPath, JSON.stringify(config));
        const result = run();
        writeFileSync(configPath, original);
        if (result.status === 0) failures.push(`${path} accepted ${flag}=${value}`);
        else refusal(result, new RegExp(`${path}.*${flag}`));
      }
      for (const field of ["include", "exclude"]) {
        const config = object(configPath);
        config[field] = ["typescript/check.ts"];
        writeFileSync(configPath, JSON.stringify(config));
        refusal(run(), new RegExp(`${path}.*${field}`));
        writeFileSync(configPath, original);
      }
      if (path === "tsconfig.base.json") continue;
      const intermediate = join(pkg, "tsconfig.intermediate.json");
      const config = object(configPath);
      config.extends = "./tsconfig.intermediate.json";
      writeFileSync(configPath, JSON.stringify(config));
      writeFileSync(intermediate, JSON.stringify({ extends: "./tsconfig.base.json" }));
      const inheritedClean = run();
      if (inheritedClean.status !== 0) failures.push(`${path} refused clean intermediate extends`);
      writeFileSync(
        intermediate,
        JSON.stringify({ extends: "./tsconfig.base.json", compilerOptions: { noImplicitAny: false } }),
      );
      const inheritedOverride = run();
      refusal(inheritedOverride, new RegExp(`${path}.*noImplicitAny`));
      if (inheritedOverride.status === 0) failures.push(`${path} accepted inherited noImplicitAny=false`);
      writeFileSync(configPath, original);
      rmSync(intermediate);
    }
    assert.deepEqual(failures, [], failures.join("\n"));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("package owners require Node 26 and pin every dependency exactly", () => {
  for (const path of [join(source, "package.json"), resolve(source, "../practice-plugin/package.json")]) {
    const pkg = object(path);
    assert.ok(typeof pkg.engines === "object" && pkg.engines !== null && "node" in pkg.engines);
    assert.equal(pkg.engines.node, ">=26");
    for (const name of ["dependencies", "devDependencies", "overrides"]) {
      const group = pkg[name];
      assert.ok(typeof group === "object" && group !== null);
      for (const version of Object.values(group))
        assert.match(String(version), /^(?:\d+\.\d+\.\d+(?:-[\w.]+)?|\$\w+)$/);
    }
  }
});

test("initial gate admits main files by path but freezes TypeScript bytes", () => {
  const root = mkdtempSync(join(tmpdir(), "carr-typescript-base-"));
  try {
    const pkg = join(root, "mcp-server");
    cpSync(join(source, "typescript"), join(pkg, "typescript"), { recursive: true });
    const git = (...args: string[]) => execFileSync("git", args, { cwd: root, env: fixtureEnv });
    git("init", "-q");
    const legacy = "mcp-server/legacy.ts";
    const mainOnly = "mcp-server/main-only.ts";
    const js = "mcp-server/legacy.js";
    const text = "export const value = 1;\n";
    for (const path of [legacy, js]) writeFileSync(join(root, path), text);
    git("add", legacy, js);
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "before gate");
    writeFileSync(join(root, mainOnly), text);
    git("add", mainOnly);
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "later main file");
    git("update-ref", "refs/remotes/origin/main", "HEAD");
    const scope = { legacyJavaScript: [js], legacyTypeScript: [legacy, mainOnly] };
    const save = () => writeFileSync(join(pkg, "typescript/migration.json"), JSON.stringify(scope));
    save();
    const gated = ["check.ts", "migration.ts", "gate.test.ts"].map((path) => join(pkg, "typescript", path));
    assert.deepEqual(migrationIssues(pkg, gated, fixtureEnv), []);
    writeFileSync(join(root, js), "export const edited = 2;\n");
    assert.deepEqual(migrationIssues(pkg, gated, fixtureEnv), []);
    writeFileSync(join(root, legacy), `${text}export const explicitAny: any = 1;\n`);
    assert.match(migrationIssues(pkg, gated, fixtureEnv).join("\n"), /legacy.ts.*convert.*strict project/);
    for (const edited of [`${text}\n`, text.replaceAll("\n", "\r\n"), text.repeat(100)]) {
      writeFileSync(join(root, legacy), edited);
      assert.match(migrationIssues(pkg, gated, fixtureEnv).join("\n"), /legacy.ts.*strict project/);
    }
    writeFileSync(join(root, legacy), text);
    renameSync(join(root, legacy), join(pkg, "Legacy.ts"));
    scope.legacyTypeScript[0] = "mcp-server/Legacy.ts";
    save();
    assert.match(migrationIssues(pkg, gated, fixtureEnv).join("\n"), /only shrink/);
    renameSync(join(pkg, "Legacy.ts"), join(root, legacy));
    scope.legacyTypeScript[0] = legacy;
    save();
    for (const extension of ["js", "ts"]) {
      const path = `mcp-server/new.${extension}`;
      writeFileSync(join(root, path), text);
      const list = extension === "js" ? scope.legacyJavaScript : scope.legacyTypeScript;
      list.push(path);
      save();
      assert.match(migrationIssues(pkg, gated, fixtureEnv).join("\n"), /only shrink.*convert.*strict project/);
      list.pop();
      rmSync(join(root, path));
    }
    save();
    git("add", "mcp-server/typescript/migration.json");
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "frozen ceiling");
    git("rm", "-f", js);
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "converted");
    git("update-ref", "refs/remotes/origin/main", "HEAD");
    writeFileSync(join(root, js), text);
    assert.match(migrationIssues(pkg, gated, fixtureEnv).join("\n"), /New first-party JavaScript/);
    rmSync(join(root, js));
    scope.legacyTypeScript.pop();
    save();
    assert.match(migrationIssues(pkg, gated, fixtureEnv).join("\n"), /main-only.ts.*pre-existing main file, list it/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("migration admission binds PR, queue and local checks to their own base", () => {
  const root = mkdtempSync(join(tmpdir(), "carr-migration-event-"));
  try {
    const pkg = join(root, "mcp-server");
    mkdirSync(join(pkg, "typescript"), { recursive: true });
    const js = "mcp-server/legacy.js";
    const scope = join(pkg, "typescript/migration.json");
    writeFileSync(scope, JSON.stringify({ legacyJavaScript: [js], legacyTypeScript: [] }));
    writeFileSync(join(root, js), "export const legacy = 1;\n");
    const git = (...args: string[]) =>
      execFileSync("git", args, { cwd: root, env: fixtureEnv, encoding: "utf8" }).trim();
    const commit = (message: string) => {
      git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--allow-empty",
        "-qm",
        message,
      );
      return git("rev-parse", "HEAD");
    };
    git("init", "-q");
    git("add", js, "mcp-server/typescript/migration.json");
    const base = commit("base with legacy JavaScript");
    git("checkout", "-qb", "topic");
    const topic = commit("unrelated PR change");
    git("checkout", "-q", "--detach", base);
    git(
      "-c",
      "user.name=Fixture",
      "-c",
      "user.email=fixture@example.invalid",
      "merge",
      "--no-ff",
      "-qm",
      "candidate",
      topic,
    );
    const candidate = git("rev-parse", "HEAD");
    git("update-ref", "refs/remotes/origin/main", base);
    const eventPath = join(root, "event.json");
    const run = (
      mode: string,
      declaredBase: string = base,
      sha: string = candidate,
      overrides: NodeJS.ProcessEnv = {},
    ) => {
      writeFileSync(
        eventPath,
        JSON.stringify(
          mode === "pull_request"
            ? { pull_request: { base: { sha: declaredBase } } }
            : { merge_group: { base_sha: declaredBase, head_sha: sha } },
        ),
      );
      return spawnSync(
        process.execPath,
        [
          "--input-type=module",
          "-e",
          `import { migrationIssues } from ${JSON.stringify(join(source, "typescript/migration.ts"))};
         const issues = migrationIssues(${JSON.stringify(pkg)}, []);
         console.log(JSON.stringify(issues)); process.exitCode = issues.length ? 1 : 0;`,
        ],
        {
          cwd: root,
          encoding: "utf8",
          env: {
            ...fixtureEnv,
            ...(mode === "local" ? {} : { GITHUB_EVENT_NAME: mode, GITHUB_EVENT_PATH: eventPath, GITHUB_SHA: sha }),
            ...overrides,
          },
        },
      );
    };
    for (const mode of ["pull_request", "merge_group", "local"]) assert.equal(run(mode).status, 0);
    git("checkout", "-q", "--detach", base);
    git("rm", "-q", js);
    const newer = commit("main converts legacy JavaScript");
    git("update-ref", "refs/remotes/origin/main", newer);
    git("checkout", "-q", "--detach", candidate);
    for (const mode of ["pull_request", "merge_group", "local"]) {
      const result = run(mode);
      assert.equal(result.status, 0, `${mode} changed after main advanced: ${result.stdout}${result.stderr}`);
      const repeated = run(mode);
      assert.equal(repeated.status, 0, repeated.stderr);
    }
    writeFileSync(join(pkg, "new.js"), "export const added = 1;\n");
    assert.match(run("pull_request").stdout, /New first-party JavaScript.*new.js/);
    rmSync(join(pkg, "new.js"));
    for (const badBase of ["", "origin/main", "0".repeat(40), topic, newer]) {
      for (const mode of ["pull_request", "merge_group"]) {
        const result = run(mode, badBase);
        assert.notEqual(result.status, 0, `${mode} admitted bad base ${badBase}`);
        assert.match(result.stderr, /migration baseline/);
      }
    }
    for (const mode of ["pull_request", "merge_group"]) {
      for (const overrides of [{ GITHUB_EVENT_PATH: join(root, "missing.json") }, { GITHUB_SHA: "" }]) {
        const result = run(mode, base, candidate, overrides);
        assert.notEqual(result.status, 0);
        assert.match(result.stderr, /migration baseline/);
      }
      assert.notEqual(run(mode, base, newer).status, 0);
    }
    git("checkout", "-q", "--detach", newer);
    writeFileSync(join(root, js), "export const resurrected = 1;\n");
    git("add", js);
    const queueCandidate = commit("queue candidate resurrects converted JavaScript");
    const resurrection = run("merge_group", newer, queueCandidate);
    assert.equal(resurrection.status, 1, resurrection.stderr);
    assert.match(resurrection.stdout, /New first-party JavaScript.*legacy.js/);
    git("update-ref", "-d", "refs/remotes/origin/main");
    const absentLocal = run("local");
    assert.notEqual(absentLocal.status, 0);
    assert.match(absentLocal.stderr, /migration baseline/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
