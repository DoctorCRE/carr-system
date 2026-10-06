#!/usr/bin/env node

// Live actuator for source-merge-policy.js.  Authority is resolved through the
// deployed controller/store, while every GitHub fact is fetched again here. Execute mode
// requires the exact plan digest and performs a conditional SHA-bound squash
// merge; it has no deploy, release, credential-write, send, or scope-expansion
// operation.

import { pathToFileURL } from "node:url";
import {
  SOURCE_MERGE_RULESET_ID,
  evaluateSourceMerge,
  sourceMergePayload,
} from "../src/source-merge-policy.js";

const API = "https://api.github.com";
const DECISION_ID = "4eaae0e1-f3b0-4e5d-af93-c44f39adc687";

function exactLocator(value) {
  const keys = ["decision_id", "head_sha", "pr_number", "repository", "ruleset_id", "work_request"];
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === keys.sort().join(",") &&
    value.decision_id === DECISION_ID &&
    value.repository === "jbookout/carr-system" &&
    typeof value.work_request === "string" && value.work_request.length > 0 &&
    /^[0-9a-f]{40}$/.test(value.head_sha || "") &&
    Number.isInteger(value.pr_number) && value.pr_number > 0 &&
    value.ruleset_id === SOURCE_MERGE_RULESET_ID;
}

export class McpSourceMergeAuthorityClient {
  constructor(token, fetchImpl = globalThis.fetch, url = process.env.CARR_MCP_URL || "https://api.doctorcre.com/mcp") {
    if (!token) throw new Error("CARR_SOURCE_MERGE_READER_TOKEN is required");
    this.token = token;
    this.fetch = fetchImpl;
    this.url = url;
  }

  async resolve(locator) {
    const response = await this.fetch(this.url, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${this.token}` },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: {
        name: "source-merge-authority",
        arguments: { decision_id: locator.decision_id,
          ...(locator.work_request ? { work_request: locator.work_request } : {}),
          pr_number: locator.pr_number, head_sha: locator.head_sha },
      } }),
    });
    const body = await response.json();
    if (!response.ok || body?.error) throw new Error("source_merge_authority_transport_refused");
    const result = body?.result || {};
    const raw = result.content?.[0]?.text;
    if (result.isError || typeof raw !== "string") throw new Error("source_merge_authority_refused");
    let value;
    try { value = JSON.parse(raw); } catch { throw new Error("source_merge_authority_shape_invalid"); }
    if (value?.schema_version !== "source-merge-authority.v1" || !value.passport)
      throw new Error("source_merge_authority_shape_invalid");
    return value;
  }
}

export class GitHubSourceMergeClient {
  constructor(token, fetchImpl = globalThis.fetch, api = API) {
    if (!token) throw new Error("GITHUB_TOKEN is required");
    if (typeof fetchImpl !== "function") throw new Error("fetch implementation is required");
    this.token = token;
    this.fetch = fetchImpl;
    this.api = api.replace(/\/$/, "");
  }

  async request(method, path, body = undefined) {
    const response = await this.fetch(this.api + path, {
      method,
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${this.token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    let value = {};
    try { value = text ? JSON.parse(text) : {}; } catch { value = { message: text.slice(0, 300) }; }
    if (!response.ok) throw new Error(`github_http_${response.status}:${String(value?.message || "unknown").slice(0, 200)}`);
    return value;
  }

  async all(path) {
    const rows = [];
    for (let page = 1; page <= 10; page += 1) {
      const separator = path.includes("?") ? "&" : "?";
      const batch = await this.request("GET", `${path}${separator}per_page=100&page=${page}`);
      if (!Array.isArray(batch)) throw new Error("github_pagination_shape_invalid");
      rows.push(...batch);
      if (batch.length < 100) return rows;
    }
    throw new Error("github_pagination_limit_reached");
  }

  async allCheckRuns(path) {
    const rows = [];
    for (let page = 1; page <= 10; page += 1) {
      const separator = path.includes("?") ? "&" : "?";
      const value = await this.request("GET", `${path}${separator}per_page=100&page=${page}`);
      const batch = value?.check_runs;
      if (!Array.isArray(batch)) throw new Error("github_check_runs_shape_invalid");
      rows.push(...batch);
      if (batch.length < 100) return rows;
    }
    throw new Error("github_check_runs_pagination_limit_reached");
  }

  async snapshot(repository, number, rulesetId) {
    const root = `/repos/${repository}`;
    const pr = await this.request("GET", `${root}/pulls/${number}`);
    const [files, reviews, checks, statuses, listedRulesets, ruleset, commit] = await Promise.all([
      this.all(`${root}/pulls/${number}/files`),
      this.all(`${root}/pulls/${number}/reviews`),
      this.allCheckRuns(`${root}/commits/${encodeURIComponent(pr.head.sha)}/check-runs`),
      this.all(`${root}/statuses/${encodeURIComponent(pr.head.sha)}`),
      this.all(`${root}/rulesets?includes_parents=true`),
      this.request("GET", `${root}/rulesets/${rulesetId}`),
      this.request("GET", `${root}/git/commits/${encodeURIComponent(pr.head.sha)}`),
    ]);
    return {
      pull_request: {
        number: pr.number,
        state: pr.state,
        draft: pr.draft === true,
        mergeable: pr.mergeable,
        mergeable_state: pr.mergeable_state,
        base: { ref: pr.base.ref, sha: pr.base.sha },
        head: { sha: pr.head.sha, tree_sha: commit?.tree?.sha },
        labels: (pr.labels || []).map(item => item.name),
        review_comments: pr.review_comments || 0,
        reviews,
        files: files.map(item => ({ filename: item.filename, previous_filename: item.previous_filename || null, status: item.status })),
        check_runs: checks,
        commit_statuses: statuses.map(item => ({ context: item.context, created_at: item.created_at, sha: item.sha, state: item.state, updated_at: item.updated_at })),
      },
      repository_ruleset: ruleset,
      repository_rulesets: listedRulesets.map(item => ({ enforcement: item.enforcement, id: item.id, target: item.target })),
    };
  }

  async merge(repository, number, eligibility) {
    return this.request("PUT", `/repos/${repository}/pulls/${number}/merge`, sourceMergePayload(eligibility));
  }

  async openCandidates(repository) {
    const rows = await this.all(`/repos/${repository}/pulls?state=open&base=main&sort=created&direction=asc`);
    return rows.map(pr => ({
      number: pr.number,
      draft: pr.draft === true,
      head_sha: pr.head?.sha,
    }));
  }
}

export async function planOrExecute({ command, locator, resolveAuthority, token, expectedDigest = null, fetchImpl = globalThis.fetch }) {
  if (!exactLocator(locator)) throw new Error("source merge locator is invalid");
  if (!["plan", "execute"].includes(command)) throw new Error("source merge command is invalid");
  if (typeof resolveAuthority !== "function") throw new Error("canonical source merge authority resolver is required");
  const client = new GitHubSourceMergeClient(token, fetchImpl);
  const resolved = await resolveAuthority(locator);
  const { passport, ...authorization } = resolved;
  const live = await client.snapshot(locator.repository, locator.pr_number, locator.ruleset_id);
  const eligibility = evaluateSourceMerge({
    authorization,
    passport,
    ...live,
  });
  if (command === "plan") return { ...eligibility, outcome: eligibility.eligible ? "eligible" : "refused" };
  if (!eligibility.eligible) throw new Error(`source_merge_refused:${eligibility.reason_codes.join(",")}`);
  if (!expectedDigest || expectedDigest !== eligibility.eligibility_digest)
    throw new Error("source_merge_eligibility_digest_changed");

  // Re-read GitHub first, then canonical controller authority. GitHub's merge
  // endpoint atomically re-enforces both the exact head SHA and the pinned
  // protected-main ruleset. That ruleset must retain strict required status
  // checks, so a base advance makes this head stale and GitHub refuses it.
  // The final store read closes the controller-authority side of the race.
  const current = await client.snapshot(locator.repository, locator.pr_number, locator.ruleset_id);
  const currentResolved = await resolveAuthority(locator);
  const { passport: currentPassport, ...currentAuthorization } = currentResolved;
  const currentEligibility = evaluateSourceMerge({
    authorization: currentAuthorization,
    passport: currentPassport,
    ...current,
  });
  if (!currentEligibility.eligible || currentEligibility.eligibility_digest !== expectedDigest)
    throw new Error("source_merge_evidence_changed");
  const merged = await client.merge(locator.repository, locator.pr_number, currentEligibility);
  if (merged?.merged !== true || typeof merged?.sha !== "string")
    throw new Error(`source_merge_github_refused:${String(merged?.message || "unknown").slice(0, 200)}`);
  return {
    ...currentEligibility,
    outcome: "merged",
    merge_sha: merged.sha,
    github_message: merged.message || null,
  };
}

export async function autoMergeOnce({ resolveAuthority, token, fetchImpl = globalThis.fetch }) {
  if (typeof resolveAuthority !== "function") throw new Error("canonical source merge authority resolver is required");
  const repository = "jbookout/carr-system";
  const client = new GitHubSourceMergeClient(token, fetchImpl);
  const candidates = await client.openCandidates(repository);
  const refused = [];
  for (const candidate of candidates) {
    if (candidate.draft || !/^[0-9a-f]{40}$/.test(candidate.head_sha || "")) {
      refused.push({ pr_number: candidate.number, reason: "canonical_pr_identity_missing" });
      continue;
    }
    const locator = {
      decision_id: DECISION_ID,
      work_request: null,
      pr_number: candidate.number,
      head_sha: candidate.head_sha,
      repository,
      ruleset_id: SOURCE_MERGE_RULESET_ID,
    };
    let resolved;
    try {
      resolved = await resolveAuthority(locator);
    } catch (error) {
      if (error?.message === "source_merge_authority_refused") {
        refused.push({ pr_number: candidate.number, reason: error.message });
        continue;
      }
      throw error;
    }
    const { passport, ...authorization } = resolved;
    const live = await client.snapshot(repository, candidate.number, SOURCE_MERGE_RULESET_ID);
    const eligibility = evaluateSourceMerge({ authorization, passport, ...live });
    if (!eligibility.eligible) {
      refused.push({ pr_number: candidate.number,
        reason_codes: eligibility.reason_codes });
      continue;
    }
    const current = await client.snapshot(repository, candidate.number, SOURCE_MERGE_RULESET_ID);
    const currentResolved = await resolveAuthority(locator);
    const { passport: currentPassport, ...currentAuthorization } = currentResolved;
    const currentEligibility = evaluateSourceMerge({
      authorization: currentAuthorization,
      passport: currentPassport,
      ...current,
    });
    if (!currentEligibility.eligible ||
        currentEligibility.eligibility_digest !== eligibility.eligibility_digest)
      throw new Error("source_merge_evidence_changed");
    const merged = await client.merge(repository, candidate.number, currentEligibility);
    if (merged?.merged !== true || typeof merged?.sha !== "string")
      throw new Error(`source_merge_github_refused:${String(merged?.message || "unknown").slice(0, 200)}`);
    return { ...currentEligibility, outcome: "merged", merge_sha: merged.sha,
      github_message: merged.message || null, scanned_candidates: candidates.length };
  }
  return { outcome: "no_eligible_candidate", scanned_candidates: candidates.length, refused };
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  if (command === "auto" && rest.length === 0) return { command };
  const value = { command };
  for (let index = 0; index < rest.length; index += 2) {
    const key = rest[index];
    const item = rest[index + 1];
    if (!key?.startsWith("--") || item === undefined) throw new Error("source merge arguments are invalid");
    value[key.slice(2).replaceAll("-", "_")] = item;
  }
  const allowed = new Set(["command", "decision_id", "work_request", "pr_number", "head_sha", "expected_eligibility_digest"]);
  if (Object.keys(value).some(key => !allowed.has(key)))
    throw new Error("source merge arguments contain an unsupported field");
  for (const key of ["decision_id", "work_request", "pr_number", "head_sha"])
    if (!value[key]) throw new Error(`--${key.replaceAll("_", "-")} is required`);
  return value;
}

async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const authorityClient = new McpSourceMergeAuthorityClient(process.env.CARR_SOURCE_MERGE_READER_TOKEN);
  if (args.command === "auto") {
    const result = await autoMergeOnce({
      resolveAuthority: value => authorityClient.resolve(value),
      token: process.env.GITHUB_TOKEN,
    });
    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
    return;
  }
  const locator = {
    decision_id: args.decision_id,
    work_request: args.work_request,
    pr_number: Number(args.pr_number),
    head_sha: args.head_sha,
    repository: "jbookout/carr-system",
    ruleset_id: SOURCE_MERGE_RULESET_ID,
  };
  const result = await planOrExecute({
    command: args.command,
    locator,
    resolveAuthority: value => authorityClient.resolve(value),
    token: process.env.GITHUB_TOKEN,
    expectedDigest: args.expected_eligibility_digest || null,
  });
  const rendered = JSON.stringify(result, null, 2) + "\n";
  process.stdout.write(rendered);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    process.stderr.write(`source-merge-controller: REFUSED: ${error.message}\n`);
    process.exitCode = 2;
  });
}
