#!/usr/bin/env node
// Minimal Claude-facing MCP proxy for the three Claude continuity verbs.
// The bearer stays in the existing 0600 token file and never enters Claude's
// JSON configuration, argv, transcript, or tracked source.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

import { selectLocalClientCredential, tokenFileSecurityIssue } from "./local-client-auth.mjs";

// Codex is opt-in; the existing Claude invocation and three-tool contract stay intact.
const CODEX = process.argv.includes("--codex");
const { REFERENCE_MANIFEST_LIMIT, SEMANTIC_STATE_LIMIT, splitReferenceManifest } = CODEX
  ? await import("./continuity-reference-manifest.mjs")
  : {};
const PROFILE = CODEX ? "codex-continuity" : "claude-continuity";
const ALLOWED = new Set(CODEX ? ["codex-checkpoint", "codex-read-recovery"] :
  ["claude-checkpoint", "claude-read-recovery", "claude-record-event"]);
const TOKEN_FILE = process.env.CARR_MCP_ENV || path.join(os.homedir(), ".config/carr/mcp-tokens.env");
const URL = process.env.CARR_MCP_URL || "https://api.doctorcre.com/mcp";
const MAX_MESSAGE_BYTES = 2_000_000;

function credential() {
  const stat = fs.lstatSync(TOKEN_FILE);
  const issue = tokenFileSecurityIssue({
    mode: stat.mode, uid: stat.uid, isFile: stat.isFile(),
    isSymbolicLink: stat.isSymbolicLink(),
  }, process.getuid?.());
  if (issue) throw new Error(`refusing MCP token file: ${issue}`);
  const selected = selectLocalClientCredential(
    { ...process.env, CARR_MCP_CLIENT_PROFILE: PROFILE },
    fs.readFileSync(TOKEN_FILE, "utf8"),
  );
  if (!selected.token) throw new Error(`missing ${selected.tokenVariable} in secure token file`);
  return selected.token;
}

function checkpointPreflight(state) {
  try {
    const packed = splitReferenceManifest(state);
    if (packed.semantic_state_bytes > SEMANTIC_STATE_LIMIT)
      return "codex_continuity_payload_too_large";
    if (packed.reference_manifest_bytes > REFERENCE_MANIFEST_LIMIT)
      return "codex_reference_manifest_too_large";
  } catch {
    // The Worker validates the complete input contract. This proxy only shares
    // the physical byte budget so it cannot create a divergent client schema.
  }
  return null;
}

async function forward(message, token) {
  if (message.method === "tools/call" && !ALLOWED.has(message.params?.name))
    return { jsonrpc: "2.0", id: message.id, error: { code: -32601, message: CODEX ? "not_in_codex_continuity_profile" : "not_in_claude_continuity_profile" } };
  if (CODEX && message.method === "tools/call" && message.params?.name === "codex-checkpoint") {
    const rejection = checkpointPreflight(message.params.arguments?.state ?? {});
    if (rejection)
      return { jsonrpc: "2.0", id: message.id, error: { code: -32602, message: rejection } };
  }
  const response = await fetch(URL, {
    method: "POST",
    ...(CODEX ? { signal: AbortSignal.timeout(30_000) } : {}),
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify(message),
  });
  if (!response.ok) throw new Error(`Worker returned HTTP ${response.status}`);
  if (message.id === undefined) return null;
  const result = await response.json();
  if (message.method === "initialize" && result?.result) {
    result.result.serverInfo = { name: CODEX ? "carr-codex-continuity" : "carr-continuity", version: "1" };
    result.result.instructions =
      (CODEX ? "Codex continuity only: record semantic checkpoints with codex-checkpoint using the " :
        "Claude continuity only: record semantic milestones with claude-checkpoint using the ") +
      "native activation binding and compare-and-swap expected_version. Never infer completion " +
      "from telemetry and never replay pending external effects.";
  }
  if (message.method === "tools/list" && Array.isArray(result?.result?.tools))
    result.result.tools = result.result.tools.filter(tool => ALLOWED.has(tool?.name));
  return result;
}

const token = credential();
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  if (!line.trim()) continue;
  let message;
  try {
    if (Buffer.byteLength(line) > MAX_MESSAGE_BYTES) throw new Error("MCP message too large");
    message = JSON.parse(line);
    const response = await forward(message, token);
    if (response) process.stdout.write(JSON.stringify(response) + "\n");
  } catch (error) {
    if (message?.id !== undefined)
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: message.id,
        error: { code: -32603, message: "continuity_proxy_failure" } }) + "\n");
    else
      process.stderr.write("carr-continuity proxy: refused malformed or oversized input\n");
  }
}
