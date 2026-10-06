# CARR Control Room — Phase 0 prototype

**Local-only by design.** This directory is a fixture-backed, read-only
Phase 0 prototype (`package.json` version `0.0.0-phase0`), served for review
with `npm run serve` (`python3 -m http.server 4173` from this directory) and
exercised by `npm test`. It has no deploy configuration on purpose: its
`contracts/phase0-manifest.v1.json` boundary is "discovery, contracts,
prototypes, inventories, validation, and council preparation only; zero
production mutation routes or actions", records `production_authorization:
false`, and lists "Control Room auth is enforced" and "ops.doctorcre.com is
deployed" as prohibited claims. It has no authentication, no CSP headers
(`http.server` sends none), and serves the whole directory, including
`contracts/` and `fixtures/`, so it must not be exposed beyond loopback. The
hosted `/control-room` route on the DoctorCRE app is a separate surface built
in the doctorcre-app repository, not this directory. Do not add a deploy
pipeline here; production construction waits on the gate in the manifest.
