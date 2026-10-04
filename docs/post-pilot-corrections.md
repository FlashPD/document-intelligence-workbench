# Post-pilot record corrections

Codex inspected the source pages and prepared separate corrected drafts for the two residual OCR errors. The [correction report](../evals/post-pilot-corrections-2026-10-03.json) binds the source images, original archive, code and before/after records. The [original pilot](review-pilot.md#recorded-author-results) retains its timing, approvals, exports and quality scores.

| Development case | Field | Original value | Corrected draft | Revision |
|---|---|---|---|---|
| `inv-f03-27` | Fourth row description | `Illustration set 3}` | `Illustration set` | 5 → 6 |
| `inv-f06-04` | Invoice number | `FO06-004` | `F06-004` | 1 → 2 |

The invoice number affects record identification; the description includes extraneous quantity/punctuation. Both corrections are supported by the visible pages. No amounts changed. Draft rescoring gives exact required headers on 6/6 cases and exact rows on 20/20. This is a maintenance diagnostic using known development labels, not improved human-pilot or held-out extractor performance. The original human results remain 5/6 and 19/20, with active median 62.459 seconds.

## Inspect and approve the drafts

Open the prepared local session in the normal workbench:

```sh
PYTHONPATH=src python3.12 -m docwork.cli serve \
  --db artifacts/post-pilot-corrections-2026-10-03/review.sqlite \
  --objects artifacts/post-pilot-corrections-2026-10-03/objects
```

Use the printed session URL, inspect the corrected description and invoice number against their pages, then approve each current revision and export JSON/CSV. Historical approvals cannot authorize these new drafts; export remains blocked until approval. This session is separate from the human study and does not add trial time or alter its report.

On a fresh checkout, prepare a new session and report first:

```sh
PYTHONPATH=src python3.12 scripts/correct_pilot_records.py prepare \
  --output-dir artifacts/post-pilot-corrections-2026-10-03 \
  --report artifacts/post-pilot-corrections-report.json
```

Verify the retained correction evidence offline:

```sh
PYTHONPATH=src python3.12 scripts/correct_pilot_records.py verify \
  evals/post-pilot-corrections-2026-10-03.json
```

Verification restores the original archive into a temporary directory, reproduces the new revisions and draft scores, checks that export is blocked, and reproduces the original pilot report. It never approves drafts or runs a new human trial. Corrections do not change OCR rules, prompts or frozen evaluation predictions.
