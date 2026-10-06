// Client-side reuse; credential and budget admission remain in askJev.
const MODEL = "jev-1.13.0";
const entries = new Map();
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}
export async function cachedSemanticAsk(askJev, request, version) {
  request = structuredClone(request);
  if (request.model !== MODEL || !version) throw new Error("pin semantic model and question-set version");
  const questions = Object.fromEntries(Object.entries(request.questions).sort().map(([k,q]) =>
    [k, q.type === "choice" ? {...q, criteria:Object.fromEntries(Object.entries(q.criteria).sort())} : q]));
  const material = canonical({...request, questions, question_set_version:version});
  if (material.length > 100000) throw new Error("narrow semantic input before asking");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(material));
  const cache_key = Array.from(new Uint8Array(digest), b=>b.toString(16).padStart(2,"0")).join("");
  const cached = entries.get(cache_key);
  if (cached && Date.now()-cached.at < 86400000) return {...await cached.value, cache_hit:true};
  const value = (async () => {
    const result = await askJev({...request, questions});
    if (result?.model && result.model !== MODEL) throw new Error("resolved model differs from pin");
    if (!result?.answers || canonical(Object.keys(result.answers).sort()) !== canonical(Object.keys(questions).sort()))
      throw new Error("incomplete semantic answer");
    for (const [key,q] of Object.entries(questions)) {
      const a = result.answers[key], value = a?.[q.type];
      const valid = a && (!a.type || a.type === q.type) && (q.type === "choice"
        ? Object.hasOwn(q.criteria, value)
        : typeof value === "number" && Number.isFinite(value) && value >= 0 &&
          value <= (q.type === "noul" ? 1 : q.criteria.length-1));
      if (!valid) throw new Error("invalid semantic answer value");
    }
    return {...result, advisory_only:true, cache_key, question_set_version:version};
  })();
  entries.set(cache_key,{at:Date.now(),value});
  while (entries.size > 512) entries.delete(entries.keys().next().value);
  try { return await value; } catch (error) { entries.delete(cache_key); throw error; }
}
export function clearSemanticCache() { entries.clear(); }
