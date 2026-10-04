# Assisted author review-time pilot

The workbench now has an explicit timed pilot mode. It opens six declared development invoices in a fixed order, records server-side interaction/pause events, and binds completion to the current approval and an existing hash-verified JSON export. The session uses a separate database and timing file. It does not change extraction settings or held-out evaluation inputs.

**Human results are pending.** The [candidate automated Chrome verification](../evals/portfolio-candidate-pilot-browser-2026-10-03/report.json) checks source highlighting, pause/resume, locked reviewer labels, approval, export, and completion on a disposable fixture. Its [screenshot](../evals/portfolio-candidate-pilot-browser-2026-10-03/review.png) demonstrates the interface. Those automated clicks are not human timing or productivity evidence. The [earlier check](../evals/review-pilot-browser-2026-10-03-v3/report.json) remains historical.

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

Open the one-time loopback URL printed by `serve`. Enter your reviewer audit label, then click **Start next invoice**. Future sources and suggestions cannot be opened before their trial starts. Review every header and every row against the page; click fields to inspect their source. Rotate sideways pages and check both pages of the last invoice. Correct extraction errors from the source. If the document itself contains an arithmetic conflict, retain the printed amount and acknowledge the issue with a reason. Approval follows the normal validation policy.

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

Reviewer labels are unauthenticated audit labels. This is a small author study on synthetic development invoices with prior familiarity. Local hash checks are integrity checks, not signed proof of human participation. No human result or percentage improvement is asserted by automated tests.

## Verify the interface

Deterministic checks cover timing arithmetic, idle exclusion, paused mutations, event retries, disk failures, competing servers, interrupted trials, source drift, final quality, and tampered records/exports. A real Chrome check exercises the controls in an isolated profile:

```sh
PYTHONPATH=src python3.12 scripts/verify_pilot_browser.py \
  --output-dir artifacts/review-pilot-browser-fresh
```

It uses the installed macOS Chrome path by default; pass `--chrome /path/to/chrome` on another supported host. The temporary fixture trial, database, and browser profile are removed afterward. Only the explicitly labeled automation report and screenshot remain.
