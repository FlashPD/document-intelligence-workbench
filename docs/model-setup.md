# Fresh-checkout model setup and offline workflow

Last updated: October 4, 2026, America/Chicago. This verifier addresses the explicit model/runtime setup portion of B07/G14. Full v1 acceptance still follows the [release contract](v1-release-contract.md).

On the supported Apple Silicon Mac, install Python 3.12, start Docker Desktop and build the [pinned parser](parser-build.md). Then run:

```sh
make model-setup-verify OUTPUT=artifacts/model-setup-fresh
```

This is an explicit download command: it fetches approximately 2.50 GB of pinned model weights, the 11 MB runtime archive and license texts into a separate temporary checkout. Allow at least 3 GiB of additional free disk space plus room for the source copy and runtime scratch. It copies tracked and nonignored working-source files, excluding existing `artifacts/`, virtual environments and Git metadata. It does not install Python/Docker or rebuild the parser; those host prerequisites have separate evidence. It checks current source rather than a published tag or remote clone.

Seven ordered checks verify the network policy, refusal of missing assets, explicit setup, offline asset verification, offline cached-fetch reuse, live model workflow and saved-workflow integrity. Missing assets must fail before creating runtime/download artifacts. Setup verifies weights/archive sizes and SHA-256 against [the profile](../config/model-mac-instruct.json); license texts are retained by hash as acquisition records, not independently pinned upstream identities.

After setup, `/usr/bin/sandbox-exec` denies outbound internet connections for the Python verifier and its descendant native model process. Numeric loopback and local Unix sockets remain permitted for HTTP inference/review, Docker and native runtime services. A probe requires an `EPERM` external-connect failure in both the parent and a newly spawned child, and verifies loopback connectivity. This is a per-process policy; it does not disconnect the Mac or constrain the preexisting Docker daemon/services. Parser containers separately use the existing network-none boundary and never pull images during document processing.

The live check uses only the two hash-pinned fictional PNG fixtures and a disposable database. It performs real Docker OCR and pinned-model extraction, rejects export before approval, preserves a conflicting observed total, tests a correction/stale edit, approves the fixture revision and verifies JSON/CSV downloads and reopened persistence. Fixture approvals are automated integration checks, not human-study results. The model runtime has its own `--offline` flag, ephemeral credentials and owned shutdown; logs redact its credential. The enclosing verifier requires unchanged source and removal of owned runtime scratch. All temporary setup assets are removed when it exits.

To exercise the documented transfer alternative without downloading another weights copy:

```sh
make model-setup-verify ASSET_SOURCE=. OUTPUT=artifacts/model-transfer-fresh
```

This explicitly copies verified weights/archive and their existing license files from the selected repository's cache into the empty checkout; it uses independent files rather than links. The report says `verified-local-transfer`, distinguishing it from network acquisition. A subsequent cached `models fetch` must succeed under the offline policy. This path proves portable cached-asset setup, not upstream download availability.

Audit a saved bundle without Docker or model inference:

```sh
PYTHONPATH=src python3.12 scripts/verify_model_setup.py \
  --verify artifacts/model-setup-fresh
```

Use a new output directory for every live run. Reports and logs preserve failed attempts; a failed/interrupted/incomplete schedule cannot pass saved verification. Each command owns a separate process session. On timeout or Ctrl+C, the wrapper receives an interrupt and gets up to thirty seconds to unwind model/parser cleanup before remaining owned descendants are stopped. If forced termination prevents parser cleanup, inspect the failed workflow's exact container before retrying; a failure cannot certify shutdown. The saved verifier checks exact artifact inventory, paths/hashes, network policy/probe, ordered checks, profile/assets, source snapshots and the nested model-workflow evidence. These are local integrity records, not signed independent attestations. This check does not establish browser behavior, genuine-scan quality, semantic judgments, whole-application memory, new-machine setup, or final release publication.

## Recorded verification

The [October 4 evidence bundle](../evals/model-setup-2026-10-04/README.md) retains both setup modes, each with all seven checks passing, independent offline verification, both real fictional workflows and owned runtime shutdown/scratch removal. The download run binds the initial verifier; the transfer run binds its final timeout/interruption cleanup. All **375 deterministic tests** and **eight isolated-checkout checks** pass. Integration timings overlap checkout work and do not replace the controlled performance study. G14 setup is covered for this working-source configuration; final-release source/tag confirmation remains B07.
