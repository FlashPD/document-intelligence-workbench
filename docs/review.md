# Local review and export slice

This is the first durable product slice after the OCR feasibility spike. It operates on the committed, self-authored PNG fixtures only. SQLite uses WAL and transactionally stores document submissions, immutable record revisions, issue decisions, approvals, export manifests, and review events. Two submissions with the same source hash receive separate document IDs and approvals.

## Try it

From the repository root, run:

```sh
PYTHONPATH=src python3.12 -m docwork.cli review seed samples/conflicting-total.png
```

Copy `document_id` from the JSON response and set `DOC_ID` to that value. The default database is `artifacts/review.sqlite`; exports are written below `artifacts/exports/`.

```sh
DOC_ID=your_document_id_here
PYTHONPATH=src python3.12 -m docwork.cli review show "$DOC_ID"
PYTHONPATH=src python3.12 -m docwork.cli review acknowledge "$DOC_ID" TOTAL_MISMATCH fields.total --revision 1 --actor demo-reviewer --reason 'Verified the printed total against the source'
PYTHONPATH=src python3.12 -m docwork.cli review approve "$DOC_ID" --revision 1 --actor demo-reviewer
PYTHONPATH=src python3.12 -m docwork.cli review export "$DOC_ID" --format json
PYTHONPATH=src python3.12 -m docwork.cli review export "$DOC_ID" --format csv
PYTHONPATH=src python3.12 -m docwork.cli review history "$DOC_ID"
```

To correct the total instead, use `review edit "$DOC_ID" fields.total 270.00 --revision 1 --actor demo-reviewer`. This creates revision 2. Review its issues, then approve revision 2 and export. A stale edit or approval is refused. An edit after approval creates a new revision and needs a new approval; the prior export remains bound to its old revision.

The JSON export includes the record, page spans and boxes, issues, decisions, source hash, record hash, and approval hash. CSV writes separate header and line-item files with values, origins, missing reasons, and evidence IDs. Text cells that could become spreadsheet formulas are escaped. Export retries return the same manifest after checking file hashes; changed or missing files are reported as an error.

## Current boundary

This CLI assumes a trusted local operator. It has no identity verification or authorization. The `--actor` value is an audit label, not an authenticated reviewer. A [loopback browser prototype](browser.md) uses the same revisions and approvals. Bounded uploads and a container worker can supply multi-page candidates, but real container execution still needs verification on a host with a running Docker daemon. The experimental local model profile is contract-tested but has not been run or scored with a real model. Do not use it for sensitive or arbitrary invoices.
