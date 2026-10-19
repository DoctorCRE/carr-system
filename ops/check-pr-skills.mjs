import { readFileSync } from "node:fs";

const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"));
const pr = event.pull_request;
if (!pr) throw new Error("PR event required");
const body = (pr.body ?? "").replace(/<!--[\s\S]*?(?:-->|$)/g, "");
const declared = body.split(/\r?\n/).some(line => /^Skills used:[ \t]*\S/i.test(line));
const dependency = ["dependabot[bot]", "renovate[bot]", "renovate"].includes(pr.user?.login);
const release = ["jbookout", "github-actions[bot]"].includes(pr.user?.login)
  && pr.head?.repo?.full_name === event.repository?.full_name
  && /^release\/schema-snapshot-[0-9a-f]{8}$/.test(pr.head?.ref ?? "")
  && body.startsWith("Opened by ops/release-pipeline.py:");
if (!declared && !dependency && !release) {
  console.error("PR body requires a non-empty Skills used: line.");
  process.exitCode = 1;
}
