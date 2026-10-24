#!/usr/bin/env bash
# Bound mirror, apt lock and package-script hangs; retry the installation once.
set -euo pipefail
install_postgres() {
  sudo install -d /usr/share/postgresql-common/pgdg
  sudo curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    "https://www.postgresql.org/media/keys/ACCC4CF8.asc"
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    | sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq postgresql-17 postgresql-18 postgresql-client-18 postgresql-17-pgvector postgresql-18-pgvector
  /usr/lib/postgresql/17/bin/initdb --version
  /usr/lib/postgresql/18/bin/initdb --version
  echo "/usr/lib/postgresql/17/bin" >> "$GITHUB_PATH"
}
export -f install_postgres
for attempt in 1 2; do
  if timeout --kill-after=5s 110s bash -euo pipefail -c install_postgres; then
    exit 0
  fi
  echo "PostgreSQL install attempt $attempt failed" >&2
done
exit 1
