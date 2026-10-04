# Assisted author review-time pilot

The workbench now has an explicit timed pilot mode. It opens six declared development invoices in a fixed order, records server-side interaction/pause events, and binds completion to the current approval and an existing hash-verified JSON export. The session uses a separate database and timing file. It does not change extraction settings or held-out evaluation inputs.

**Human results: all six author trials completed.** The [results page](../evals/author-review-pilot-2026-10-03-results.html) and [original report](../evals/author-review-pilot-2026-10-03/report.json) retain timing, corrections and final quality, including two remaining errors. The [candidate automated Chrome verification](../evals/portfolio-candidate-pilot-browser-2026-10-03/report.json) separately checks source highlighting, pause/resume, locked reviewer labels, approval, export, and completion on a disposable fixture. Its [screenshot](../evals/portfolio-candidate-pilot-browser-2026-10-03/review.png) demonstrates the interface. Those automated clicks are not human timing or productivity evidence. The [earlier check](../evals/review-pilot-browser-2026-10-03-v3/report.json) remains historical.

The two errors have separate [post-pilot corrected drafts](post-pilot-corrections.md) for normal review and fresh approval. They do not replace the original study results below.

## Recorded author results

| Case | Active seconds | Final revision | Field edits | Acknowledgments |
|---|---:|---:|---:|---:|
| Clean single page | 140.992 | 1 | 0 | 0 |
| Printed total conflict | 130.710 | 1 | 0 | 1 |
| Low contrast | 71.858 | 5 | 4 | 0 |
| Skew | 53.060 | 2 | 1 | 0 |
| Sideways page | 51.791 | 1 | 0 | 0 |
| Two pages | 38.608 | 1 | 0 | 0 |

The recorded active and elapsed medians are both **62.459 seconds**. All six scheduled trials completed; the first includes 1.050 paused seconds and the recorded idle total is zero. Five edit events and one printed-conflict acknowledgment were recorded. These are interaction-based review measurements, not extraction latency or a manual-entry comparison.

Final required headers are exact on **5/6** eligible documents, header macro F1 is **0.9833**, and exact-row F1 is **0.9500** with **19/20** exact rows. Quantities, unit prices and line totals are correct on all twenty rows. The low-contrast case retains description `Illustration set 3}` instead of `Illustration set`; the two-page case retains invoice number `FO06-004` instead of `F06-004`. Original suggestions, decisions and approved exports remain unchanged after inspecting these errors. Completing the approval workflow does not establish semantic correctness.

The [portable archive](../evals/author-review-pilot-2026-10-03/manifest.json) contains the supplied report byte-for-byte, protocol, source snapshot, raw timing events, and a verified SQLite/artifact backup. The backup rebases export paths and compacts SQLite pages; approved records, revision history, approval hashes and original JSON/CSV export bytes remain intact. Verify it offline from the repository root:

```sh
PYTHONPATH=src python3.12 scripts/archive_review_pilot.py verify \
  evals/author-review-pilot-2026-10-03 --require-complete
```

Verification restores only into a temporary directory and reproduces the report. To supply a portable session to a later release audit, restore into a new ignored directory:

```sh
PYTHONPATH=src python3.12 scripts/archive_review_pilot.py restore \
  evals/author-review-pilot-2026-10-03 \
  --output-dir artifacts/author-pilot-restored
```

Retain the original archive rather than serving its restored copy for another study. Local integrity checks do not authenticate the participant. Author familiarity, six fixed synthetic development cases, the sixty-second activity cutoff and lack of a manual baseline limit the findings; there is no time-saved or general workforce claim.

## Declared study

This is an assisted-review-only author pilot. Fixed OCR/rules v0.3 candidates and canonical OCR pages come from the verified development freeze, with provenance labeled `replay_ocr_rules`. OCR, parsing, and model processing are excluded from review time. Opening the first page after clicking Start is included.

| Order | Document | Declared case |
|---|---|---|
| 1 | `inv-f02-02` | Clean single-page invoice |
| 2 | `inv-f01-12` | Printed total conflict |
| 3 | `inv-f03-27` | Low contrast |
| 4 | `inv-f04-29` | Skew |
| 5 | `inv-f05-30` | Sideways page |
| 6 | `inv-f06-04` | Two-page invoice |

Selection uses one document per development family and declared corpus treatments. It is fixed rather than random and not screened for successful completion. Every scheduled invoice remains in the report, including abandoned and unstarted trials. Each can be timed only once in a session; a fresh session is a new study, not a retry that improves the original result.

The pilot measures review effort and final eligible field/row quality. It cannot establish time saved, workforce productivity, arbitrary real-invoice accuracy, or safe unattended processing. A future manual-versus-assisted comparison needs separate matched difficulty groups and counterbalanced order, avoiding retyping remembered invoices.

## Run it

No Docker, model, or downloads are required. Python 3.12, the committed corpus, and its frozen development predictions are sufficient. From the repository root:

```sh
PYTHONPATH=src python3.12 scripts/review_pilot.py prepare \
  --output-dir artifacts/review-pilot-author-001
PYTHONPATH=src python3.12 scripts/review_pilot.py serve \
  artifacts/review-pilot-author-001
```

Open the loopback session URL printed by `serve`. Check the server-established **Signed in as** identity, then click **Start next invoice**. Future sources and suggestions cannot be opened before their trial starts. Review every header and every row against the page; click fields to inspect their source. Rotate sideways pages and check both pages of the last invoice. Correct extraction errors from the source. If the document itself contains an arithmetic conflict, retain the printed amount and acknowledge the issue with a reason. Approval follows the normal validation policy.

Click **Approve revision**, create **Export JSON**, then click **Finish trial**. A missing approval, changed revision, missing export, or corrupted export prevents completion. Start the next invoice when ready. If you cannot finish a case, click **Unable to finish**; it remains incomplete and cannot be repeated within this session.

Pause before stepping away. Switching tabs pauses automatically; resume explicitly. Reloading the page pauses an existing trial. Restarting the server marks its open trial abandoned because a monotonic clock cannot reliably span restarts or reboots. An exclusive local lock prevents competing pilot servers.

After reviewing, stop the server with Ctrl-C and audit a report at a new path:

```sh
PYTHONPATH=src python3.12 scripts/review_pilot.py report \
  artifacts/review-pilot-author-001 --output artifacts/review-pilot-author-001-report.json
```

Exit 0 means every trial completed; exit 2 means the report retains incomplete trials. A newly prepared session reports zero completed trials and null measured-time summaries. The report records outcomes, corrections/acknowledgments, final revision/record/approval/export hashes, completed-only timing medians, and completed-only eligible quality. It checks the original candidate bytes and approved export before scoring. Publish the protocol, raw timing events, and immutable exports alongside any author results.

## Timing method and limits

The server records monotonic offsets at receipt of start, interaction, pause, resume, finish, and abandon events. The UI sends throttled pointer/key interactions without typed text or document content. No automatic heartbeat keeps an unattended page active.

For a running interval, count up to 60 seconds since the previous event as active and classify excess as idle. Paused intervals count separately. Active + idle + paused equals the recorded elapsed interval. Reading without an interaction beyond the cutoff can be undercounted; server receipt times include network delay. Raw events allow that assumption to be inspected. Event retransmissions do not count twice, and failed disk writes retain the last committed state.

The archived October 3 reviewer labels are unauthenticated audit labels. Current browser sessions bind new trial actors to the [server-established local reviewer](access.md); this does not authenticate the historical study or independently prove human participation. This is a small author study on synthetic development invoices with prior familiarity. Local hash checks are integrity checks, not signed proof of human participation. No human result or percentage improvement is asserted by automated tests.

## Verify the interface

Deterministic checks cover timing arithmetic, idle exclusion, paused mutations, event retries, disk failures, competing servers, interrupted trials, source drift, final quality, and tampered records/exports. A real Chrome check exercises the controls in an isolated profile:

```sh
PYTHONPATH=src python3.12 scripts/verify_pilot_browser.py \
  --output-dir artifacts/review-pilot-browser-fresh
```

It uses the installed macOS Chrome path by default; pass `--chrome /path/to/chrome` on another supported host. The temporary fixture trial, database, and browser profile are removed afterward. Only the explicitly labeled automation report and screenshot remain.
