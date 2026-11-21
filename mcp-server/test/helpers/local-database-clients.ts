type DatabaseClient = {
  once(event: "end", listener: () => void): unknown;
  end(): Promise<void>;
  connection: { stream: { destroy(): unknown } };
};

type DatabasePool = {
  on(event: "connect", listener: (client: DatabaseClient) => void): unknown;
  end(): Promise<void>;
};

const CLOSE_TIMEOUT_MS = 2_000;

export function trackLocalDatabaseClients() {
  const pending = new Map<DatabaseClient, Promise<void>>();
  return {
    watch(pool: DatabasePool) {
      pool.on("connect", client => {
        const closed = new Promise<void>(resolve => client.once("end", () => {
          pending.delete(client);
          resolve();
        }));
        pending.set(client, closed);
      });
    },
    async close(pools: Iterable<DatabasePool>, stopDatabase: () => Promise<void>) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const deadline = new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error(
            `Local CARR PostgreSQL clients did not close within ${CLOSE_TIMEOUT_MS} ms`,
          )), CLOSE_TIMEOUT_MS);
        });
        // Pool.end() can resolve while idle client sockets are still closing.
        const drained = Promise.allSettled([...pools].map(pool => pool.end()))
          .then(() => Promise.all(pending.values()));
        await Promise.race([drained, deadline]);
      } finally {
        clearTimeout(timer);
        try {
          for (const client of pending.keys()) {
            void client.end();
            client.connection.stream.destroy();
          }
        } finally {
          pending.clear();
          await stopDatabase();
        }
      }
    },
  };
}
