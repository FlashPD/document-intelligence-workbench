# Document lifecycle verification

This B02 record verifies local processing and lifecycle controls on the working source as of October 4, 2026. The [report](report.json) records seven passing checks, the fictional input SHA-256, immutable Docker parser image ID, runtime/UI source hashes and screenshot hashes. [Review-ready](review-ready.png) and [reprocessed](reprocessed.png) show native Chrome controls. The [test log](tests.log) records 330 passing deterministic tests; it includes injected parser/model fixtures and does not measure inference or human effort.

Six live checks use the production background HTTP server, native Chrome batch/reprocess/cancel/delete controls and real Docker/Tesseract on `samples/clean.png`. The seventh deliberately substitutes a sleeping Python command under the same production container resource policy to confirm removal of an active owned parser before cancellation settles. That probe is separate from the real OCR checks.

Reproduce with Docker and Chrome installed, from a fresh source checkout after building its parser:

```sh
make parser-build
make lifecycle-verify OUTPUT=artifacts/lifecycle-fresh-001
make test
```

Choose a new output directory. The verifier owns disposable SQLite/artifact/browser state and cleans it after the run; screenshots and the source-bound report remain. This is a working-tree runtime record, not a published-tag verification, model inference run, real-scan evaluation, performance acceptance or human productivity study. B03–B07 remain open in the [backlog](../../docs/backlog.md).
