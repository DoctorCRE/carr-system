const CLOSE_TIMEOUT_MS = 2_000;

export function trackLocalDatabaseClients() {
  const pending = new Map();
  return {
    watch(pool) {
      pool.on('connect', client => {
        const closed = new Promise(resolve => client.once('end', () => {
          pending.delete(client);
          resolve();
        }));
        pending.set(client, closed);
      });
    },
    async close(pools, stopDatabase) {
      let timer;
      try {
        const deadline = new Promise((_, reject) => {
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
