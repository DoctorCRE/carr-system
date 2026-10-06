#!/usr/bin/env node
import { readFileSync, lstatSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { selectLocalClientCredential, tokenFileSecurityIssue } from '../mcp-server/local-client-auth.mjs';
import { ERROR_RESPONSE } from '../mcp-server/src/runtime-errors.js';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export async function consumeRuntimeErrors(control, callVerb) {
  const plan = await control('/_runtime-errors/plan', {});
  if (!plan.ok || !Array.isArray(plan.operations) || typeof plan.health !== 'string') throw new Error('invalid_runtime_error_plan');
  const failures = [];
  for (const operation of plan.operations) {
    try {
      if (!['add-loop', 'update-loop', 'close-loop'].includes(operation.verb)) throw new Error('invalid_runtime_error_operation');
      let args = { ...operation.args, idempotency_key: operation.key };
      if (operation.verb !== 'add-loop' && !Number.isInteger(args.base_version)) {
        const { loop } = await callVerb('read-loop', { loop_id: args.loop_id });
        if (!Number.isInteger(loop?.version)) throw new Error('runtime_error_loop_unreadable');
        if (loop.status !== 'open' || loop.version !== operation.expected_version) {
          const refresh = await control('/_runtime-errors/refresh', { fingerprint: operation.fingerprint, key: operation.key,
            reason: loop.status !== 'open' ? 'loop_not_open' : 'human_edit' });
          if (!refresh.ok) throw new Error('runtime_error_refresh_refused');
          continue;
        }
        const prepared = await control('/_runtime-errors/prepare', { fingerprint: operation.fingerprint, key: operation.key, base_version: loop.version });
        if (!prepared.ok || !prepared.operation) throw new Error('runtime_error_operation_unprepared');
        args = { ...prepared.operation.args, idempotency_key: operation.key };
      }
      const result = await callVerb(operation.verb, args);
      if (['version_conflict', 'loop_not_open'].includes(result.error)) {
        const refresh = await control('/_runtime-errors/refresh', { fingerprint: operation.fingerprint, key: operation.key, reason: result.error });
        if (!refresh.ok) throw new Error('runtime_error_refresh_refused');
        if (result.error === 'loop_not_open') continue;
      }
      if (!result.ok || !result.loop_id) throw new Error(result.error || 'runtime_error_loop_write_failed');
      const ack = await control('/_runtime-errors/ack', { fingerprint: operation.fingerprint, key: operation.key, loop_id: result.loop_id });
      if (!ack.ok) throw new Error('runtime_error_ack_refused');
    } catch (error) {
      failures.push(error);
      try { await control('/_runtime-errors/defer', { fingerprint: operation.fingerprint, key: operation.key }); }
      catch (deferError) { failures.push(deferError); }
    }
  }
  if (failures.length) throw new AggregateError(failures, failures.map(error => error.message).join('; '));
  return plan.operations.length ? (await control('/_runtime-errors/plan', {})).health : plan.health;
}

export function parseVerbFailure(error) {
  try { return JSON.parse(error.stdout); } catch { /* Refusals are emitted on stderr. */ }
  const payload = String(error.stderr || '').match(/TOOL ERROR\s+(\{[\s\S]*\})\s*$/);
  if (payload) {
    try { return JSON.parse(payload[1]); } catch { /* Preserve a bounded transport cause below. */ }
  }
  throw new Error('runtime_error_verb_unavailable', { cause: error });
}

function credential() {
  const path = process.env.CARR_MCP_ENV || resolve(homedir(), '.config/carr/mcp-tokens.env');
  const stat = lstatSync(path);
  const issue = tokenFileSecurityIssue({ isFile: stat.isFile(), isSymbolicLink: stat.isSymbolicLink(), mode: stat.mode, uid: stat.uid }, process.getuid?.());
  if (issue) throw new Error('runtime_error_token_file_refused');
  const selected = selectLocalClientCredential({ ...process.env, CARR_MCP_CLIENT_PROFILE: 'local' }, readFileSync(path, 'utf8'));
  if (!selected.token) throw new Error('runtime_error_token_unavailable');
  return selected.token;
}

async function main() {
  const token = credential();
  const origin = new URL(process.env.CARR_MCP_URL || 'https://api.doctorcre.com/mcp').origin;
  const control = async (path, body) => {
    const response = await fetch(origin + path, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify(body), signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error('runtime_error_control_unavailable');
    const result = await response.json();
    if (result.error) throw new Error('runtime_error_control_refused');
    return result;
  };
  const callVerb = async (verb, args) => {
    const options = { cwd: ROOT, encoding: 'utf8', timeout: 30000, stdio: ['ignore', 'pipe', 'pipe'], env: { PATH: process.env.PATH, HOME: homedir(), CARR_MCP_CLIENT_PROFILE: 'local', ...(process.env.CARR_MCP_URL ? { CARR_MCP_URL: process.env.CARR_MCP_URL } : {}), ...(process.env.CARR_MCP_ENV ? { CARR_MCP_ENV: process.env.CARR_MCP_ENV } : {}) } };
    try { return JSON.parse(execFileSync(resolve(ROOT, 'run.sh'), ['call', verb, JSON.stringify(args)], options)); }
    catch (error) { return parseVerbFailure(error); }
  };
  const health = process.argv.includes('--shadow')
    ? (await control('/_runtime-errors/plan', { observe_only: true })).health
    : await consumeRuntimeErrors(control, callVerb);
  if (typeof health !== 'string' || !/^(OK|WARN) runtime errors/.test(health)) throw new Error('invalid_runtime_error_health');
  mkdirSync(resolve(ROOT, 'out'), { recursive: true });
  if (!process.argv.includes('--shadow')) {
    writeFileSync(resolve(ROOT, 'out/runtime-error-consumer-installed.json'), JSON.stringify({ workflow: 'runtime-error-watch', installed: true }) + '\n');
    writeFileSync(resolve(ROOT, 'out/runtime-error-health.json'), JSON.stringify({ checked_at: new Date().toISOString(), health }) + '\n');
  }
  console.log(health);
  console.log(`runtime-error-watch: mode=${process.argv.includes('--shadow') ? 'shadow' : 'live'} result=${health.split(' ')[0]} ${process.argv.includes('--shadow') ? 'writes=0' : 'receipt=written'}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { await main(); } catch (error) {
    const cause = (error instanceof AggregateError ? error.errors : [error]).map(item => /^[a-z_]+$/.test(item.message) ? item.message : item.name).join(', ');
    console.error(`UNAVAILABLE runtime errors — ${cause} · ${ERROR_RESPONSE}`); process.exitCode = 1;
  }
}
