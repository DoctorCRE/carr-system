// Structured clones keep storage independent of each object instance.
export function handoffState() {
  const values = new Map();
  let alarm = null;
  const storage = {
    async get(key) { return structuredClone(values.get(key)); },
    async put(key, value) { values.set(key, structuredClone(value)); },
    async delete(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) values.delete(key); },
    async list({ prefix }) { return new Map([...values].filter(([key]) => key.startsWith(prefix)).map(([k, v]) => [k, structuredClone(v)])); },
    async getAlarm() { return alarm; },
    async setAlarm(time) { alarm = time; },
    async deleteAlarm() { alarm = null; },
    async transaction(fn) { return fn(storage); },
  };
  return { storage };
}
