# Worker recovery

Production deployment runs through `.github/workflows/deploy.yml` after a green
main canary. The workflow applies pending migrations with `tools/migrate.py` and
checks the production `/release` identity and verb count.

Repair source through a normal pull request and green CI. A migration changes
the recovery decision because an older Worker must remain compatible with the
current schema. Applied migrations and their checksums stay immutable.

Durable Object migration tags also remain applied after a Worker version
changes. Check the deployed tag before choosing a recovery version. The
workflow refuses unapplied Durable Object migrations unless its explicit
manual migration input is selected.
