# Bounded production-parser queue

The [October 3 report](../evals/parser-queue-2026-10-03/report.json) measures a fixed batch of **20 fictional development originals** through the production intake and Docker worker, with fresh parsing and OCR/rules extraction. All 20 reached `REVIEW_READY`; none were approved or exported. The batch contains 16 PNGs and four original two-page PDFs, producing 24 rendered pages. The [standalone table](../evals/parser-queue-2026-10-03/index.html) shows every document's wait, processing time, completion time, and outcome.

| Measurement | Recorded result |
|---|---|
| Scheduled / review-ready / failed | 20 / 20 / 0 |
| Fully submitted batch to final completion | 41.799 seconds |
| Sum of worker processing calls | 41.712 seconds |
| Worker processing P50 / P95 | 1.794 / 3.412 seconds |
| Final job wait after batch submission | 38.215 seconds |

These are serial queue observations, not controlled warm/cold latency or concurrent capacity. The run declared a concurrent frozen invoice model workload, but did not inspect its process state. A later October 3 inspection at 3:51 PM America/Chicago found no project model runner or coordinator; the model log's last write preceded this benchmark. Those facts do not establish the precise termination time or actual overlap. The archived declaration is retained as an unverified assumption; these timings cannot support a claim of performance under model load. No model inference was performed by the benchmark. A review-ready outcome means a candidate exists, not that its fields or rows are correct. Validation warnings remain in the saved predictions, and every export still requires human approval. See [evaluation recovery](evaluation-recovery.md).

## Protocol and timing boundaries

The selection is fixed by document ID before execution: documents 01–03 from each of the six development families, plus document 04 from families 01 and 06. All originals are hash-verified against the committed corpus. Test/calibration documents and saved OCR predictions are excluded. Gold labels are checked only by corpus verification and are not passed to production extraction. This sample does not cover arbitrary layouts, degraded scans, maximum-size inputs, or all supported document classes.

One durable store submits the batch before a single serial worker drains it. Every job runs `process_one(..., reparse=True)` against the same immutable local parser image ID. The benchmark does not reuse checkpoints or retry failed jobs to improve its outcome. Each successful job must have exactly one processing attempt, no inherited approval, and a checkpoint belonging to the frozen image.

Wait starts when the complete batch has been submitted. It excludes each earlier document's wait while intake is still submitting the rest of the batch. Processing starts immediately before the worker call and ends immediately after it returns; it includes job claim, image lookup, Docker startup, PDF rendering/image decoding, OCR, import/checkpoint verification, rules extraction, validation, and persistence. It excludes subsequent report serialization, human review, model extraction, and server startup. Queue drain includes the gaps between worker calls. P50/P95 use nearest rank across all 20 calls, including failed calls if any occur. No service-level objective was tuned or promoted from these results.

The host is recorded as macOS arm64 / Python 3.12.12. Container limits are the production two CPUs, 1 GiB memory, 64 PIDs, and 600-second document deadline. Limits describe configuration; actual peak memory, Docker VM memory, power use, and thermal state were not measured. Model-versus-rules timings and the clean-sample real-model objective remain separate evidence.

## Reproduce and audit

Build the parser image explicitly, then use a new output directory. Declare observed concurrent workloads rather than labeling a loaded host idle:

```sh
make parser-build
PYTHONPATH=src python3.12 scripts/benchmark_queue.py run \
  --output-dir artifacts/parser-queue-fresh \
  --co-running-workload 'none observed'
PYTHONPATH=src python3.12 scripts/benchmark_queue.py verify \
  artifacts/parser-queue-fresh
```

The runner never downloads an image or model. It writes a new workbench, the frozen selection/image/source identity, atomic progress measurements, all predictions including failures, a JSON report, and HTML. Interrupted runs retain their partial measurements and have no complete report; start at a new destination rather than discarding failed completed calls. Exit 2 means a fully recorded batch contains processing failures. A missing parser image or changed source cannot publish complete evidence.

The verifier checks all 20 unique jobs, serial finite timings, failure accounting, summary recomputation, source/manifest hashes, source identities, page counts, artifact hashes, and the regenerated presentation. Source drift remains visible without rewriting historical measurements. These are local consistency checks, not signed attestation.

The committed bundle retains raw measurements, predictions, source snapshot, and reports. The mutable benchmark database and rendered objects remain in ignored `artifacts/` and are outside the archived measurement inventory. The [deterministic log](../evals/queue-release-contracts-2026-10-03.log) records 263 passing tests, including failure timing, incomplete/duplicate jobs, changed predictions, relabeled summaries, and presentation checks. The benchmark does not add an extraction-quality score or relax the broader release criteria.
