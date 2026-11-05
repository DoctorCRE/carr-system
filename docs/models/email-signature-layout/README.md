# Signature layout repair

The decoder's `body::html_to_text` folds HTML block boundaries into spaces.
Signature extraction previously required a standalone `--` line and labeled fields.
The private decoded `html` column retains the original block layout.

`_load` carries that column as `signature_html` on the same admitted message row.
The signature planner restores block breaks without changing activity text, router
input, source identity, contact matching, or the existing finding journal.
The parser excludes quoted history before it searches for one sender-bound contact
run. An unlabeled run needs the sender's email and an adjacent whole phone line,
or a whole line combining both. The run must end within the final twelve nonempty current-message lines. Competing addresses, malformed addresses,
duplicate fields, multiple runs and oversized runs hold the observation.
Only labeled names and companies become identity observations.

A saved extraction-policy version forces one new scan when the parser changes,
even if the store is unchanged. Successful scans restore the ordinary skip.

The statechart covers the existing proposal journal as well as extraction.
It is a design sketch, not a model-checked proof. The existing canonical source
and journal models remain unchanged. The new tests map these transitions:

- `test_observed_non_delimited_contact_runs` covers positive bounded contact runs.
- `test_non_delimited_runs_hold_competing_or_prose_or_history` covers refused regions.
- `test_signature_html_preserves_contact_layout_and_excludes_history` covers HTML and quotation boundaries.
- `test_html_signature_uses_proposal_journal_and_preserves_activity_body` covers a lost acknowledgement, frozen replay and no updater.
- `test_signature_extraction_version_reloads_unchanged_store_once` covers policy invalidation and the restored skip.
- `test_loaded_signature_html_is_bound_to_its_message_without_changing_body` covers the SQLite loader and preserved identity.

Run `python3 ops/email-contact-candidates-selftest.py`,
`python3 ops/email-contact-review-selftest.py`, and
`python3 ops/outlook-intake-selftest.py`.

The read-only research-copy comparison is per decoded row. It is not a mailbox
coverage or contact-match accuracy claim. Synthetic expected values are authored
independently of the parser. Real-copy extraction yield is not labeled precision.
No model prompt or AI output changes.

The shipped lifecycle contract already refuses terminal-source actions and records
prior-action notes. The J12 intake job was minted but undispatched. This research
copy exports no message lifecycle evidence. No decoder field, tombstone table or
intake lifecycle effect is invented here. Missing mail and Deleted-folder membership
are not deletion evidence. Lifecycle acceptance remains blocked on a verified
positive source event and its source/revision binding.
