(async () => {
// The short loader verifies this exact file before eval. expected, tools,
// store, and load are lexical bindings supplied by the functions.exec isolate.
const runbookStoreKey = "carr_engineering_runbook_body_v1";
const decodeOne = (result, label) => {
  const blocks = Array.isArray(result?.content)
    ? result.content.filter((item) => item?.type === "text" && typeof item.text === "string")
    : [];
  if (blocks.length !== 1) throw new Error(label + " returned an unsupported native CallToolResult");
  return JSON.parse(blocks[0].text);
};
const refuse = (reason) => { throw new Error("engineering source hydration refused: " + reason); };
const isObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const bareSha256 = (value) => {
  const match = typeof value === "string" ? /^(?:sha256:)?([0-9a-f]{64})$/.exec(value) : null;
  return match ? match[1] : null;
};
// The Codex functions.exec isolate exposes no crypto, TextEncoder, Buffer, or
// require.  UTF-8 encoding and SHA-256 are therefore computed from basic
// ECMAScript primitives only; lone surrogates encode as U+FFFD exactly as the
// doctrine store's TextEncoder did when it sealed content_hash.
const utf8Bytes = (value) => {
  const bytes = [];
  for (let index = 0; index < value.length; index += 1) {
    let code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff && index + 1 < value.length) {
      const low = value.charCodeAt(index + 1);
      if (low >= 0xdc00 && low <= 0xdfff) {
        code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
        index += 1;
      }
    }
    if (code >= 0xd800 && code <= 0xdfff) code = 0xfffd;
    if (code < 0x80) bytes.push(code);
    else if (code < 0x800) bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    else if (code < 0x10000) bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    else bytes.push(0xf0 | (code >> 18), 0x80 | ((code >> 12) & 0x3f), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
  }
  return bytes;
};
const sha256Hex = (value) => {
  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const message = utf8Bytes(value);
  const bitLength = message.length * 8;
  message.push(0x80);
  while (message.length % 64 !== 56) message.push(0);
  const high = Math.floor(bitLength / 0x100000000);
  const low = bitLength >>> 0;
  message.push((high >>> 24) & 0xff, (high >>> 16) & 0xff, (high >>> 8) & 0xff, high & 0xff,
    (low >>> 24) & 0xff, (low >>> 16) & 0xff, (low >>> 8) & 0xff, low & 0xff);
  const state = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
  const rotr = (word, bits) => ((word >>> bits) | (word << (32 - bits))) >>> 0;
  const w = new Array(64);
  for (let offset = 0; offset < message.length; offset += 64) {
    for (let t = 0; t < 16; t += 1) {
      const i = offset + t * 4;
      w[t] = ((message[i] << 24) | (message[i + 1] << 16) | (message[i + 2] << 8) | message[i + 3]) >>> 0;
    }
    for (let t = 16; t < 64; t += 1) {
      const s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3);
      const s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
      w[t] = (w[t - 16] + (s0 >>> 0) + w[t - 7] + (s1 >>> 0)) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = state;
    for (let t = 0; t < 64; t += 1) {
      const s1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
      const ch = ((e & f) ^ (~e & g)) >>> 0;
      const temp1 = (h + s1 + ch + K[t] + w[t]) >>> 0;
      const s0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
      const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
      const temp2 = (s0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    const round = [a, b, c, d, e, f, g, h];
    for (let index = 0; index < 8; index += 1) state[index] = (state[index] + round[index]) >>> 0;
  }
  return state.map((word) => word.toString(16).padStart(8, "0")).join("");
};
const sourceInput = {work_request: expected.work_request_ref};
const source = decodeOne(await tools.mcp__carr__engineering_passport_source(sourceInput), "engineering-passport-source");
if (source?.schema_version !== "engineering-passport-source.v1") refuse("unexpected passport source schema");
const work = source.work_request;
const plan = source.accepted_plan_revision;
if (!isObject(work) || !isObject(plan)) refuse("passport source omitted the Work Request or accepted plan");
if (work.ref !== expected.work_request_ref) refuse("passport source resolved a different Work Request ref");
if (work.id !== expected.work_request.id
    || Number(work.version) !== expected.work_request.state_version
    || work.canonical_record_digest !== expected.work_request.canonical_record_digest)
  refuse("current Work Request id/version/digest do not match the controller plan binding");
if (plan.plan_ref !== expected.accepted_plan_revision.id
    || Number(plan.revision) !== expected.accepted_plan_revision.revision
    || plan.digest !== expected.accepted_plan_revision.digest)
  refuse("current accepted plan ref/revision/digest do not match the controller plan binding");
const caps = isObject(plan.caps) ? plan.caps : {};
const sourceMerge = caps.source_merge;
let sourceMergeProjection = null;
if (sourceMerge !== undefined && sourceMerge !== null) {
  if (!isObject(sourceMerge)
      || Object.keys(sourceMerge).sort().join(",") !== "authorized_paths,base_branch,repository,schema_version")
    refuse("accepted caps.source_merge is malformed");
  if (sourceMerge.schema_version !== "source-merge-scope.v1") refuse("accepted caps.source_merge schema_version is invalid");
  if (typeof sourceMerge.repository !== "string"
      || !/^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(sourceMerge.repository))
    refuse("accepted caps.source_merge repository is invalid");
  if (typeof sourceMerge.base_branch !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._\/-]*$/.test(sourceMerge.base_branch))
    refuse("accepted caps.source_merge base_branch is invalid");
  const paths = sourceMerge.authorized_paths;
  if (!Array.isArray(paths) || paths.length === 0) refuse("accepted caps.source_merge authorized_paths is empty or not a list");
  for (const [index, path] of paths.entries()) {
    if (typeof path !== "string" || !/^[!-~]+$/.test(path) || path.startsWith("/") || path.includes("\\")
        || /(^|\/)\.\.(\/|$)/.test(path))
      refuse("accepted caps.source_merge authorized_paths[" + index + "] is invalid");
    if (index > 0 && !(paths[index - 1] < path)) refuse("accepted caps.source_merge authorized_paths are not unique and C-sorted");
  }
  sourceMergeProjection = {
    schema_version: sourceMerge.schema_version, repository: sourceMerge.repository,
    base_branch: sourceMerge.base_branch, authorized_paths: paths.slice(), path_count: paths.length,
  };
} else if (expected.source_merge_required === true) {
  refuse("the accepted slice names source_merge but the accepted plan carries no caps.source_merge");
}
const runbook = isObject(plan.preimage) ? plan.preimage.runbook : undefined;
if (!isObject(runbook) || Object.keys(runbook).sort().join(",") !== "content_hash,ref,revision_id,section_id")
  refuse("accepted plan preimage.runbook pointer is malformed");
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
if (!uuidPattern.test(runbook.section_id) || !uuidPattern.test(runbook.revision_id)
    || typeof runbook.ref !== "string" || !runbook.ref.trim())
  refuse("accepted plan preimage.runbook identifiers are invalid");
const acceptedHash = bareSha256(runbook.content_hash);
if (!acceptedHash) refuse("accepted runbook content_hash is not a sha256 digest");
const sectionsInput = {section_ids: [runbook.section_id]};
const doc = decodeOne(await tools.mcp__carr__doctrine_sections(sectionsInput), "doctrine-sections");
if (doc?.ok !== true || !Array.isArray(doc.sections) || !Array.isArray(doc.missing))
  refuse("doctrine-sections returned an unsupported response");
if (doc.missing.length !== 0) refuse("accepted runbook section is missing from the doctrine store");
if (doc.sections.length !== 1) refuse("doctrine-sections did not return exactly one runbook section");
const section = doc.sections[0];
if (!isObject(section) || section.id !== runbook.section_id) refuse("doctrine-sections returned a different section id");
if (section.status !== "active") refuse("accepted runbook section is not active");
const currentVersion = Number(section.current_version);
if (!Number.isInteger(currentVersion) || currentVersion < 1 || String(currentVersion) !== String(section.current_version))
  refuse("accepted runbook current_version is not a positive exact integer");
const body = isObject(section.body) ? section.body.text : undefined;
if (typeof body !== "string" || body.length === 0) refuse("accepted runbook body text is unavailable");
const returnedHash = bareSha256(section.content_hash);
const computedHash = sha256Hex(body);
if (returnedHash !== acceptedHash || computedHash !== acceptedHash)
  refuse("current runbook body does not match the accepted content_hash");
store(runbookStoreKey, {section_id: section.id, current_version: currentVersion,
  content_hash: "sha256:" + acceptedHash, text: body, next: 0});
// The section selector is controller metadata. Authority remains the current
// canonical operator record, narrowed by the independently pinned scope packet.
let assignmentProjection = null;
const assignmentCalls = [];
if (expected.operator_assignment) {
  const binding = expected.operator_assignment;
  const input = {section_ids: [binding.section_id, binding.scope_section_id, binding.packet_section_id]};
  const records = decodeOne(await tools.mcp__carr__doctrine_sections(input), "operator assignment");
  assignmentCalls.push({tool_name: "mcp__carr__doctrine_sections", input});
  if (records?.ok !== true || !Array.isArray(records.sections) || records.sections.length !== 3
      || !Array.isArray(records.missing) || records.missing.length) refuse("operator assignment records missing");
  const readRecord = (id, pinnedHash) => {
    const rows = records.sections.filter((row) => row?.id === id);
    if (rows.length !== 1) refuse("operator assignment section identity mismatch");
    const row = rows[0];
    if (row.status !== "active" || !isObject(row.body) || typeof row.body.text !== "string"
        || !Number.isInteger(Number(row.current_version)) || Number(row.current_version) < 1
        || String(Number(row.current_version)) !== String(row.current_version))
      refuse("operator assignment section not active or versioned");
    const digest = sha256Hex(row.body.text);
    if (bareSha256(row.content_hash) !== digest || (pinnedHash && digest !== pinnedHash))
      refuse("operator assignment section hash mismatch");
    let data;
    try { data = JSON.parse(row.body.text); } catch { refuse("operator assignment body is not JSON"); }
    if (!isObject(data)) refuse("operator assignment body is not an object");
    return {row, data, digest};
  };
  const current = readRecord(binding.section_id, null);
  const scope = readRecord(binding.scope_section_id, binding.scope_sha256);
  const implementation = readRecord(binding.packet_section_id, binding.packet_sha256);
  const a = current.data;
  const required = ["schema_version", "state", "work_request", "accepted_plan", "accepted_plan_digest",
    "slice_ref", "envelope_id", "envelope_digest", "attempt_id", "source_main", "paths", "worktree",
    "branch", "helper_name", "scope_section_id", "scope_sha256"];
  if (Object.keys(a).sort().join(",") !== required.sort().join(",")) refuse("operator assignment shape mismatch");
  if (a.schema_version !== "engineering-source-assignment.v1" || a.state !== "assigned"
      || a.work_request !== expected.work_request_ref || a.slice_ref !== expected.slice_ref
      || a.accepted_plan !== expected.accepted_plan_revision.id
      || a.accepted_plan_digest !== expected.accepted_plan_revision.digest
      || a.envelope_id !== binding.envelope_id || a.envelope_digest !== binding.envelope_digest
      || a.attempt_id !== binding.attempt_id) refuse("operator assignment does not bind this exact attempt");
  if (a.scope_section_id !== binding.scope_section_id || a.scope_sha256 !== binding.scope_sha256)
    refuse("operator assignment does not bind the immutable scope");
  const lease = scope.data.exact_scoped_source_lease;
  if (!isObject(lease) || scope.data.work_request?.ref !== a.work_request
      || scope.data.work_request?.accepted_plan !== a.accepted_plan
      || scope.data.registered_plan?.slice_ref !== a.slice_ref)
    refuse("immutable scope does not bind the accepted slice");
  if (!Array.isArray(a.paths) || a.paths.length === 0 || JSON.stringify(a.paths) !== JSON.stringify(lease.paths)
      || a.paths.some((path, i) => typeof path !== "string" || path.startsWith("/")
        || path.split("/").some((part) => !part || part === "." || part === "..")
        || (i > 0 && !(a.paths[i - 1] < path)))) refuse("operator paths differ from immutable scope");
  if (a.worktree !== lease.worktree || a.branch !== lease.branch
      || !/^[a-z0-9][a-z0-9-]{2,79}$/.test(a.helper_name)
      || a.worktree !== "/Users/booko/carr-system/.claude/worktrees/" + a.helper_name
      || a.branch !== "codex/" + a.helper_name || !/^[0-9a-f]{40}$/.test(a.source_main))
    refuse("operator worktree, branch or source identity is invalid");
  if ("worktree:sha256:" + sha256Hex(a.worktree) !== binding.expected_worktree_ref
      || "branch:sha256:" + sha256Hex(a.branch) !== binding.expected_branch_ref)
    refuse("operator source references differ from pinned return constraints");
  if (!isObject(implementation.data.packet) || implementation.data.slice !== a.slice_ref)
    refuse("implementation packet slice mismatch");
  const supplemental = JSON.stringify({assignment: a,
    source_bindings: [current, scope, implementation].map((item) => ({section_id: item.row.id,
      current_version: Number(item.row.current_version), content_hash: "sha256:" + item.digest})),
    scope: {allowed_change: lease.allowed_change, forbidden: lease.forbidden,
      scac_constraint: lease.scac_constraint, worktree_creation_route: lease.worktree_creation_route,
      f7_overlap_evidence: scope.data.f7_overlap_evidence},
    implementation_packet: implementation.data.packet});
  store("carr_engineering_assignment_body_v1", {section_id: binding.section_id,
    current_version: Number(current.row.current_version), content_hash: "sha256:" + sha256Hex(supplemental),
    text: supplemental, next: 0});
  assignmentProjection = {...a, section_id: binding.section_id,
    current_version: Number(current.row.current_version), content_hash: "sha256:" + current.digest,
    source_main_requires_fresh_git_verification: true,
    chunk: {store_key: "carr_engineering_assignment_body_v1", size: 4000, next: 0,
      derived_body_sha256: "sha256:" + sha256Hex(supplemental)}};
}

return {
  schema_version: "engineering-source-native-projection.v1",
  provenance: "native_call_tool_result",
  source_calls: [
    {tool_name: "mcp__carr__engineering_passport_source", input: sourceInput},
    {tool_name: "mcp__carr__doctrine_sections", input: sectionsInput},
    ...assignmentCalls,
  ],
  work_request: {ref: work.ref, id: work.id, version: Number(work.version),
    canonical_record_digest: work.canonical_record_digest},
  accepted_plan_revision: {plan_ref: plan.plan_ref, revision: Number(plan.revision), digest: plan.digest},
  verification: {
    work_request_current: true, accepted_plan_current: true,
    source_merge_required: expected.source_merge_required === true,
    source_merge_present: sourceMergeProjection !== null,
    runbook_hash_verified: true,
  },
  source_merge: sourceMergeProjection,
  operator_assignment: assignmentProjection,
  runbook: {
    ref: runbook.ref, section_id: section.id, revision_id: runbook.revision_id, section_key: section.section_key,
    doc_slug: section.doc_slug, title: section.title, status: section.status, current_version: currentVersion,
    content_hash: "sha256:" + acceptedHash, body_chars: body.length,
    chunk: {store_key: runbookStoreKey, size: 4000, next: 0},
  },
};
})()
