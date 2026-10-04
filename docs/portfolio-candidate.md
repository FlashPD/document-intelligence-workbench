# Experimental portfolio candidate

This candidate demonstrates a local invoice extraction, evidence inspection, correction, approval and export workflow. The engineering result is an auditable product with measured extractor selection: the optional local model regresses against deterministic rules on the synthetic invoice test, so rules remain the default. Human approval is mandatory.

The six-case author pilot remains pending. Its absence is visible in the release audit and prevents this candidate from being described as a completed portfolio checklist. No human productivity or time-saved claim is made. The original architecture plan has a broader scope than this experimental candidate.

## Demo and evidence

```sh
make demo-replay
```

Python 3.12 is sufficient for this four-case offline demonstration. The UI labels recorded OCR; edits, approvals and downloaded JSON/CSV use the real revision and export stores. See [the walkthrough](demo-replay.md).

- [Standalone paired extraction comparison](../evals/release-comparison-2026-10-03.html): complete invoice and receipt results, uncertainty, failures, and stage-timing boundaries.
- [Model results and inspected disagreements](heldout-model-comparison.md): why the optional model was not promoted.
- [Captioned scripted recording](../evals/portfolio-candidate-browser-2026-10-03/index.html): verified keyboard, source, rotation, page and export behavior; fifteen-second screenshot sequence, without audio or elapsed-time claims.
- [Offline replay browser check](../evals/portfolio-candidate-replay-browser-2026-10-03/report.json): the four-case entrypoint and its controls.
- [Fresh parser evidence](../evals/portfolio-candidate-parser-2026-10-03-v2/report.json) and [live-model workflow](../evals/portfolio-candidate-model-workflow-2026-10-03/report.json): runtime verification on the candidate source. The [first parser attempt](../evals/portfolio-candidate-parser-2026-10-03/report.json) retains its stale-image failure; the image was rebuilt before repeating the checks.
- [Clean-source packaging check](../evals/portfolio-candidate-checkout-2026-10-03/report.json): isolated snapshot, offline verification, and demo preparation without local runtime assets.
- [Candidate audit](../evals/portfolio-candidate-audit-2026-10-03/index.html): passing and pending evidence, current-source hashes and fresh contract log.

## What to discuss in an interview

| Engineering decision | Evidence and consequence |
|---|---|
| Compare the model with a conventional baseline | Paired saved-OCR evaluation includes failed calls and row errors; a model is not adopted just because it is available |
| Keep suggestions separate from authority | Models propose records; only human approval of an exact revision permits an export |
| Resolve evidence on the server | Span IDs and geometry come from validated parser output; existence/alignment checks remain weaker than semantic attribution |
| Checkpoint parsing independently | Retry unavailable inference without rerunning the original parser; leases and fencing prevent stale publication |
| Preserve immutable exports and approvals | A correction creates a revision, stale edits fail, and downloaded artifacts retain approved hashes |
| Bound untrusted parsing | Network-denied Docker, immutable image identity, memory/PID limits, and recorded timeout/OOM recovery drills |
| Expose evidence limitations | Interrupted model metadata, uncontrolled timing, synthetic data, public receipt pretraining uncertainty and missing human results remain visible |

The demonstration and runtime probes use explicitly fictional fixture reviewers. They do not supply author-pilot outcomes. The CORD adapter is an offline domain diagnostic and is not a production receipt-review product.

## Deliberately deferred scope

The implemented stack is Python standard-library HTTP/SQLite, HTML/CSS/JavaScript, Poppler/Tesseract and optional llama.cpp. Docling, FastAPI/React migration, a VLM, PC/CUDA inference and a telemetry stack are separate workstreams. They are not implied by the original plan's diagrams.

Genuine scanner-captured development invoices, semantic evidence-attribution sampling, controlled warm/cold latency and total RAM/VRAM studies, stronger reviewer identity, document deletion/retention, and a manual-versus-assisted review study remain unverified or unimplemented. Synthetic degraded images are not genuine scanner captures. The candidate makes no accuracy claim for arbitrary vendors, handwriting, payments, tax decisions or unattended approval.

## Final release step

Complete all six declared cases in the [author pilot](review-pilot.md), then stop its server and audit the session. Retain unstarted or unfinished outcomes if any case cannot be completed. Run a new release audit with that session and the recorded demo. Review the cards, presentation and eventual release-tag checkout before publishing. The candidate itself is neither a published release nor a signed runtime attestation.

See [the release runbook](portfolio-release.md), [system card](system-card.md) and [data card](data-card.md) for setup and claim boundaries.
