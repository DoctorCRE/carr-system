# Retained deal document text

`read-deal-document-text` serves `deal-document-text.v1` and `page-text.v1`.
Its input takes `deal_id` and `attachment_id`. The first response includes the
original `content_hash`, derivative `text_hash`, `page_count`, and line fragments.
Use the response's `next` fields with the same deal and attachment on continuation.
An original hash change refuses `source_changed`. Restart the viewer from page one.

Each fragment has a one-based page and line, a zero-based Unicode code point offset,
text, and `end_of_line`. Join fragments in order, joining lines with LF. Pages are
the exact strings returned by the existing `pdftotext -layout -enc UTF-8` adapter.
Blank and trailing lines remain. Maximum response is 16000 code points and 200
fragments. The derivative holds at most 1000 pages and 500000 code points.
Documents exceeding those bounds explicitly return unavailable. OCR-only documents
return no_text. Existing image-region OCR proposals do not become full page text.
Parser failure follows the existing adapter's empty-text behavior.

The committed synthetic JSON responses are recorded from the registered verb on
real disposable PostgreSQL and checked against `response.schema.json`. Regenerate
with `node mcp-server/test/deal-document-text-postgres.test.mjs --record-fixtures`.
The normal test run compares them exactly and validates the response contract.

## Backfill after deployment

The coordinator runs `python3 bin/backfill-deal-document-text.py --dry-run` first,
then `python3 bin/backfill-deal-document-text.py`. This worker never runs either
against production. Both print one JSON summary with `ok`, `scanned`, `missing`,
`skipped`, `produced`, `no_text`, `unavailable`, `dry_run`, `last_document_ref`.
On an unchanged complete rerun, missing and all production counts are zero and
skipped equals scanned. In dry run no bytes are fetched and no writes occur.
Each invocation scans at most 10000 attachments serially. If scanned reaches the
cap, continue with `--after` set to the reported last_document_ref. Restarting
from the beginning is safe. Infrastructure failures stop the invocation with
exit 1; already persisted derivatives remain safe to replay.

Non-PDF attachments and originals outside the existing 1-byte to 25-MB parser
budget receive unavailable without parsing. Every undeleted deal attachment is
scanned, including these unsupported originals. PDF originals are read
through the existing authenticated R2 adapter and checked against stored byte size
and SHA256 before parsing. Changing originals produces a new immutable key. Old
rows remain for audit and cannot be served for current content. No model is called.

New J76 intake attachments are covered when the existing extraction job runs for
them. This PR adds no intake scheduling hook. Non-extraction intake requires the
same bounded backfill command; it is not automatically parsed on upload.

## Production proof

After deploy and backfill, the coordinator must observe the new verb in production
`list-verbs`, retain the backfill count summary, then invoke
`read-deal-document-text` for a known visible deal and attachment. Its content_hash
must equal list-deal-documents.content_hash and a separately byte-verified stored
original SHA256. Status must be produced, and the known expected text must match
exactly. Production proof remains pending until that evidence exists.

DoctorCRE/carr-system owns retention, backfill and the authenticated read.
jbookout/doctorcre-app needs no change in this PR; its viewer consumes this contract
in its own delivery. jbookout/software-factory needs no change; no orchestration
or workflow interface changes here.

The pre-code `lifecycle.mmd` states the intended lifecycle. The executable
`DealDocumentText.tla` adds the real write/read guards. Its rules refuse reading
absent, superseded, unavailable, and deleted text. `verified-lifecycle.mmd` is the checked model output. The rendered PNG remains
a local review artifact because the repository rejects opaque binary additions. The initial parallel chart
exposed missing synchronization guards in the model; the SQL source binding and
read join implement those guards. Run the statechart skill's `from-spec` command
on `DealDocumentText.tla` with TLC to reproduce the four invariant checks.

The evidence checker is `node tools/check-deal-document-text-proof.mjs RESPONSE ORIGINAL EXPECTED_TEXT`.
RESPONSE is the saved real registered read response, ORIGINAL is the independently
read stored original, and EXPECTED_TEXT is the exact concatenated line-fragment
text expected on that read. It checks the response contract, original SHA256 and
text equality. It performs no network requests and exits 0 on a match, 1 otherwise.
It does not establish deployment identity or the live registry. The coordinator
must collect those production observations separately as specified above.
