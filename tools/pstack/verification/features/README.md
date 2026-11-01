# CARR local verification map

Run the skill's Doctor first. Use synthetic fixtures only. Save receipts separately per feature and run, never as a shared append target. Feature PRs add only their own `features/<name>.md` and never edit this README. Only the upstream `/maintain-verification-skill` pass rebuilds this index, including missing feature entries.

- [Dispatch cards](dispatch.md) covers registration admission and refusal.
- [Orchestrator rotation](rotation.md) covers marker opt-in and successor instructions.
- [Gate integrity](integrity.md) covers repository hook contracts.

The primary authenticated API, database migrations and app journeys need separate integration recipes and authorization. They are not verified by these local feature paths.
