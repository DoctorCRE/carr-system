// Shared endpoint policy for disposable PostgreSQL test fixtures.
import assert from 'node:assert/strict';

export function assertDisposableDatabase(dsn, { env = process.env, service = false } = {}) {
  const refuse = () => assert.fail('REFUSED: disposable PostgreSQL endpoint required');
  let url;
  try { url = new URL(dsn); } catch { refuse(); }
  if (!['postgres:', 'postgresql:'].includes(url.protocol) || !url.port || url.search || url.hash) refuse();
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  const hosted = env.GITHUB_ACTIONS === 'true' && env.CARR_CI_POSTGRES_IMAGE === 'both'
    && url.hostname === 'postgres' && url.port === '5432' && url.username === 'carr_ci' && !url.password
    && (/^\/[a-z0-9_]+_[a-f0-9]{32}$/.test(url.pathname) || service && url.pathname === '/carr_ci');
  if (!loopback && !hosted) refuse();
  return url;
}
