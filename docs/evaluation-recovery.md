# Interrupted frozen evaluation recovery

An October 3, 2026 process inspection at **3:51 PM America/Chicago** found no project invoice evaluation runner, receipt coordinator, or llama-server. The invoice run retained 93/180 ledger-completed predictions, including four completed failures. All 93 hashes matched. Its first model session had a server log but no `runtime.json`; its exit cause, exact termination time, shutdown completion, and sampled memory measurements were unavailable.

The [interruption observation](../evals/invoice-model-heldout-2026-10-03/sessions/0001/interruption.json) binds the original log, freeze, and those 93 completed predictions. It records shutdown and sampled RSS as **null**. This is a local process observation, not signed lifecycle attestation. No runtime metadata was reconstructed from plausible settings, and completed failures remain failures. An uncommitted interrupted document may be recomputed under the original settings.

The original manifest, OCR inputs, prompt, model/runtime profile, scoring contract, and extraction source snapshot are unchanged. The public `eval-invoice-model --resume` interface validates them and all completed prediction hashes before starting another owned session. A short tool-owned resumed session was stopped gracefully to move the long workflow into a detached serialized coordinator; its real shutdown metadata remains in session 0002. The subsequent session continues the same frozen run. No receipt/model work starts concurrently with invoice inference.

## Lifecycle and memory coverage

The original domain report gathers only existing `runtime.json` files. A missing file could previously disappear behind a later successful session. Release auditing and comparison export now enumerate **every** session directory. Each needs real completed runtime metadata or an explicit hash-bound interruption observation. Missing evidence, changed logs/predictions, contradictory shutdown claims, and invented interrupted-session memory cannot pass.

An interruption observation does not establish clean shutdown or memory coverage. Exported comparison supplements show recorded runtime-session and interrupted-session counts, and qualify RSS as available samples from recorded sessions. The first session's missing RSS prevents a whole-run peak claim. Per-document inference timings cover committed predictions, including committed failures; aborted uncommitted attempts and their lost metadata are not recovered or added to those totals. They remain stage timings rather than total experiment wall time.

The progress command verifies each completion hash and shows ledger update time and sessions without runtime metadata. **A missing runtime file does not mean a process is running.** Check live process state separately before resuming; do not start a second model based on a stale completion count or an old log.

## Serialized coordinator

Stop or verify absence of other model workloads first. To let the coordinator own invoice resume and then the receipt stages:

```sh
PYTHONPATH=src python3.12 scripts/complete_release_evaluations.py \
  --invoice-run evals/invoice-model-heldout-2026-10-03 --resume-invoice \
  --receipt-root evals/cord-heldout-2026-10-03 \
  --html-output evals/release-comparison-2026-10-03.html
```

`--resume-invoice` and `--wait-for-invoice` are mutually exclusive. The resume mode owns the child invocation; wait mode is for an already confirmed running invoice process. Waiting examines the latest session's shutdown metadata, so an earlier abrupt session cannot hide a later stopped attempt forever. A stopped latest session without a report causes an explicit error after the reporting grace period. No source, prompt, threshold, or model is silently substituted.

The detached run's launch metadata and output log reside in ignored `artifacts/release-runner-2026-10-03/`. It is an owned local background process with redirected stdin/output and a separate process session. Detachment removes reliance on an interactive chat tool invocation; it does not survive an OS reboot, guarantee execution, or supply a scheduler. Check the recorded PID/log and completion ledger rather than assuming continued activity. The six-case author pilot remains a separate human task.

The earlier [parser queue](parser-queue.md) declared concurrent model load without verifying its process state. That declaration remains archived and explicitly qualified; the measurements do not establish performance under model load.

## Recovery validation

The [fresh release audit](../evals/interruption-release-audit-2026-10-03/index.html) records **271 passing contract tests**, [16 passing Docker parser checks](../evals/interruption-release-parser-2026-10-03/report.json), and verified existing browser and recording evidence. Its overall status is **pending**: the invoice and receipt model comparisons, a current real-model workflow check, and the human author review pilot are still required. Partial inference results are progress evidence only.

## Completed continuation

The continuation now publishes the [complete paired comparisons](heldout-model-comparison.md): 180 invoice and 100 receipt predictions, with every committed failure retained. Both invoice shutdown sessions and the original interruption observation remain intact. The [candidate parser refresh](../evals/portfolio-candidate-parser-2026-10-03-v2/report.json), [current model workflow](../evals/portfolio-candidate-model-workflow-2026-10-03/report.json), and [candidate audit](../evals/portfolio-candidate-audit-2026-10-03/index.html) supersede the historical pending snapshot above without rewriting it. The [completed author pilot](review-pilot.md#recorded-author-results) retains two residual errors; available invoice RSS samples still cannot recover the lost first session's memory coverage.
