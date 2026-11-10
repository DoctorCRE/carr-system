type HandoffStorage = {
  get<T = unknown>(key: string): Promise<T | undefined>;
  put(key: string, value: unknown): Promise<void>;
  delete(keys: string | string[]): Promise<void>;
  list(options: { prefix: string }): Promise<Map<string, unknown>>;
  getAlarm(): Promise<number | null>;
  setAlarm(time: number): Promise<void>;
  deleteAlarm(): Promise<void>;
  transaction<T>(fn: (storage: HandoffStorage) => T | Promise<T>): Promise<T>;
};

// Structured clones keep storage independent of each object instance.
export function handoffState() {
  const values = new Map<string, unknown>();
  let alarm: number | null = null;
  const storage: HandoffStorage = {
    async get<T = unknown>(key: string) {
      // Mirror Durable Object storage, where callers select the stored value type.
      return structuredClone(values.get(key)) as T | undefined;
    },
    async put(key, value) {
      values.set(key, structuredClone(value));
    },
    async delete(keys) {
      for (const key of Array.isArray(keys) ? keys : [keys]) values.delete(key);
    },
    async list({ prefix }) {
      return new Map([...values].filter(([key]) => key.startsWith(prefix)).map(([k, v]) => [k, structuredClone(v)]));
    },
    async getAlarm() {
      return alarm;
    },
    async setAlarm(time) {
      alarm = time;
    },
    async deleteAlarm() {
      alarm = null;
    },
    async transaction(fn) {
      return fn(storage);
    },
  };
  return { storage };
}
