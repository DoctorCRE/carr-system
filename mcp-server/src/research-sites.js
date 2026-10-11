// THE RESEARCH-SITE INDEX (Joe, 2026-10-07): "the database of research sites is
// not really meant to restrict you, its meant to point you at sites to go to for
// research, but you can go to any site not already in the database. in fact, you
// should use the full internet and if you find a new resource you would add it to
// the database for future reference. its more of an index of useful sites."
//
// So this is an INDEX, never a permission list. Nothing reads it to decide what a
// session may fetch: the egress guard (hooks/guard-unattended.py) does not know it
// exists. Research workflows read it first, search the open web beyond it, and
// add what they find. Every verb is open to every caller, sessions and scheduled
// runs included — Joe ruled he does not want to be involved in adding sites.
import { ToolError } from "./tool-error.js";

export const RESEARCH_SITE_WRITE_VERBS = new Set(["add-research-site", "remove-research-site"]);

// An exact public hostname: no scheme, path, port, credentials, wildcard or IP.
const HOST_OK = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;
const NON_PUBLIC_SUFFIX = new Set(["localhost", "local", "internal", "localdomain", "arpa", "lan"]);
// Topic tags: short lowercase words joined by hyphens ("cre-market", "npi").
const TOPIC_OK = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export function normalizeResearchHost(value) {
  const host = typeof value === "string" ? value.trim().toLowerCase().replace(/\.$/, "") : "";
  const labels = host.split(".");
  if (!HOST_OK.test(host) || host.length > 253 || labels.some(l => l.length > 63) ||
      /^[0-9.]+$/.test(host) || NON_PUBLIC_SUFFIX.has(labels[labels.length - 1]))
    // Echo the rejected value only when it is plainly a hostname attempt: a
    // user:pass@ authority or a pasted URL may carry a credential.
    throw new ToolError({ error: "research_site_host_invalid",
      host: typeof value === "string" && /^[\w.-]{0,253}$/.test(value) ? value : null,
      hint: "give one exact public hostname such as www.example.com, with no scheme, path, port, wildcard or IP address. Put a specific starting page in url." });
  return host;
}

export function normalizeTopics(value) {
  if (value === undefined || value === null)
    throw new ToolError({ error: "research_site_topics_required",
      hint: "tag what the site is good for, e.g. [\"cre-market\", \"medical-office\"]" });
  const list = Array.isArray(value) ? value : [value];
  const topics = [...new Set(list.map(t => typeof t === "string" ? t.trim().toLowerCase().replace(/[\s_]+/g, "-") : ""))];
  if (!topics.length || topics.length > 12 || topics.some(t => !TOPIC_OK.test(t) || t.length > 40))
    throw new ToolError({ error: "research_site_topics_invalid", topics: value,
      hint: "1 to 12 short tags of lowercase words joined by hyphens, e.g. cre-market, npi, veterinary" });
  return topics.sort();
}

function text(value, field, { optional = false, max = 1000 } = {}) {
  if (optional && (value === undefined || value === null || value === "")) return null;
  if (typeof value !== "string" || !value.trim() || value.length > max)
    throw new ToolError({ error: "research_site_text_invalid", field, max });
  return value.trim();
}

// A query or fragment field whose name says it carries a credential. Two tests:
// each WORD of the name (split on - _ . and at camelCase humps) against the
// short credential words, so "api_key", "X-Amz-Signature", "sortKey" and "sig"
// are refused; and the lowercased, separator-stripped name against the longer
// words as substrings, so run-together "accesstoken", "clientsecret" and
// "AWSAccessKeyId" are refused too. BENIGN_PARAM keeps ordinary words that
// only contain one ("monkey" ends in "key") acceptable.
const SENSITIVE_WORD = /^(?:token|key|keys|apikey|secret|signature|sig|password|passwd|pwd|pass|auth|authorization|credential|credentials|session|sessionid|sid|jwt|bearer)$/i;
const SENSITIVE_FLAT = /token|secret|passw|credential|signature|authorization|bearer|sessionid|key(?:s|id)?$/;
const BENIGN_PARAM = new Set(["keyword", "keywords", "monkey", "turkey", "hockey", "jockey", "whiskey", "donkey"]);

function sensitiveName(name) {
  const flat = name.toLowerCase().replace(/[-_.]/g, "");
  if (BENIGN_PARAM.has(flat)) return false;
  const words = name.replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2").split(/[\s\-_.]+/);
  return words.some(word => SENSITIVE_WORD.test(word)) || SENSITIVE_FLAT.test(flat);
}

function sensitiveFields(params) {
  return [...params.keys()].some(sensitiveName);
}

// A starting page on the listed host. It is stored and returned to every
// caller, and the add writes it into the audit event, so it must carry no
// credential: no user:pass@ authority and no token/key/secret/signature/password
// field in the query or fragment. Errors never echo the rejected url.
export function startUrl(value, host) {
  const url = text(value, "url", { optional: true, max: 2000 });
  if (url === null) return null;
  let parsed;
  try { parsed = new URL(url); } catch { parsed = null; }
  if (!parsed || !["http:", "https:"].includes(parsed.protocol) ||
      parsed.hostname.toLowerCase().replace(/\.$/, "") !== host)
    throw new ToolError({ error: "research_site_url_invalid",
      hint: "url must be an http(s) address on the same host as `host`" });
  const authority = url.replace(/^[a-z]+:\/\//i, "").split(/[/?#]/, 1)[0];
  const fragment = new URLSearchParams(parsed.hash.replace(/^#/, ""));
  if (parsed.username || parsed.password || authority.includes("@") ||
      sensitiveFields(parsed.searchParams) || sensitiveFields(fragment))
    throw new ToolError({ error: "research_site_url_credential",
      hint: "url may not carry credentials: drop any user:password@ and any token, key, secret, signature or password field, then add the plain public page" });
  return url;
}

// LIKE treats % and _ as wildcards; a free-text filter means the literal text.
function likeLiteral(value) {
  return `%${value.replace(/[\\%_]/g, "\\$&")}%`;
}

const SITE_COLUMNS = `s.host, s.url, s.topics, s.note, a.slug as added_by, s.added_at,
  s.removed_at, ra.slug as removed_by, s.removal_reason`;

export function researchSiteTools({ withEnvelope, writeEvent }) {
  return {
    "list-research-sites": {
      description: "The research-site index: sources worth checking FIRST for a research topic, each tagged with what it is good for. A starting point, never a limit — always ALSO search the open internet for sources not listed here, and add any new useful one with add-research-site. Filter by topic tag (exact, e.g. cre-market, npi, veterinary) and/or free text matched against host, tags and note. Active rows only unless include_removed is true. Returns the topic tags in use so a caller can pick one.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        topic: { type: "string", description: "one topic tag; rows carrying it are returned" },
        text: { type: "string", description: "substring matched against host, tags and note" },
        include_removed: { type: "boolean", description: "also return soft-removed rows, for history" } } },
      handler: async (c, _actor, args = {}) => {
        const where = [], params = [];
        if (args.include_removed !== true) where.push("s.removed_at is null");
        if (args.topic !== undefined) {
          params.push(normalizeTopics(args.topic)[0]);
          where.push(`s.topics @> array[$${params.length}]::text[]`);
        }
        if (args.text !== undefined) {
          params.push(likeLiteral(text(args.text, "text", { max: 200 }).toLowerCase()));
          const p = `$${params.length}`;
          where.push(`(s.host like ${p} escape '\\' or lower(coalesce(s.note,'')) like ${p} escape '\\'
                       or array_to_string(s.topics,' ') like ${p} escape '\\')`);
        }
        const r = await c.query(
          `select ${SITE_COLUMNS}
             from research_site s
             join actor a on a.id=s.added_by_actor_id
             left join actor ra on ra.id=s.removed_by_actor_id
            ${where.length ? "where " + where.join(" and ") : ""}
            order by s.removed_at is not null, s.host, s.added_at desc`, params);
        const topics = await c.query(
          "select distinct unnest(topics) as topic from research_site where removed_at is null order by 1");
        return { ok: true, sites: r.rows, topics_in_use: topics.rows.map(row => row.topic),
          reminder: "This index is a starting point. Also search the open internet, and add any new useful source with add-research-site." };
      },
    },

    "add-research-site": {
      write: true,
      description: "Add a useful research source to the index so future research checks it first. Open to every caller, sessions and scheduled runs included: add a site whenever research turns up a source worth returning to. Give the exact host (www.example.com), topic tags saying what it is good for, and optionally a starting url and a note. Adding a host already listed changes nothing and returns the existing row. The index does not grant or restrict network access.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        host: { type: "string", description: "exact public hostname, e.g. www.cbre.com" },
        topics: { type: "array", items: { type: "string" }, description: "what it is good for: 1 to 12 tags like cre-market, npi, veterinary" },
        url: { type: "string", description: "optional starting page on that host" },
        note: { type: "string", description: "optional: what to look for there" } },
        required: ["idempotency_key", "host", "topics"] },
      handler: async (c, actor, args) => {
        const host = normalizeResearchHost(args.host);
        const topics = normalizeTopics(args.topics);
        const url = startUrl(args.url, host);
        const note = text(args.note, "note", { optional: true, max: 2000 });
        return withEnvelope(c, actor, "add-research-site", args, async () => {
          // The partial unique index is the serialization point: a concurrent
          // add of the same absent host (a different idempotency key, so the
          // envelope does not serialize it) waits on the winner's row and then
          // does nothing, and the read below returns the winner's committed row.
          // No row is inserted, so no audit event is written for the loser.
          const r = await c.query(
            `insert into research_site (host, url, topics, note, added_by_actor_id)
             values ($1,$2,$3,$4,$5)
             on conflict (host) where removed_at is null do nothing
             returning id, host, added_at`, [host, url, topics, note, actor.id]);
          if (!r.rows.length) {
            const listed = await c.query(
              "select id, host, topics, url, note from research_site where host=$1 and removed_at is null", [host]);
            return { ok: true, already_listed: true, ...listed.rows[0] };
          }
          await writeEvent(c, actor, "add-research-site", "research_site", r.rows[0].id, {
            field: "host", new: { host, topics, url, note }, idempotency_key: args.idempotency_key });
          return { ok: true, id: r.rows[0].id, host, topics, added_at: r.rows[0].added_at };
        });
      },
    },

    "remove-research-site": {
      write: true,
      description: "Take a source out of the research-site index (dead, moved, or not useful). Open to every caller. The row is kept with who removed it, when and why; adding the host again later makes a new row.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        host: { type: "string", description: "the listed hostname" },
        reason: { type: "string", description: "why it no longer belongs in the index" } },
        required: ["idempotency_key", "host", "reason"] },
      handler: async (c, actor, args) => {
        const host = normalizeResearchHost(args.host);
        const reason = text(args.reason, "reason");
        return withEnvelope(c, actor, "remove-research-site", args, async () => {
          const r = await c.query(
            `update research_site set removed_at=now(), removed_by_actor_id=$2, removal_reason=$3
              where host=$1 and removed_at is null returning id, host, removed_at`, [host, actor.id, reason]);
          if (!r.rows.length) throw new ToolError({ error: "research_site_not_listed", host });
          await writeEvent(c, actor, "remove-research-site", "research_site", r.rows[0].id, {
            field: "removed_at", old: { host }, new: { removed_at: r.rows[0].removed_at, reason },
            idempotency_key: args.idempotency_key });
          return { ok: true, id: r.rows[0].id, host, removed_at: r.rows[0].removed_at };
        });
      },
    },
  };
}
