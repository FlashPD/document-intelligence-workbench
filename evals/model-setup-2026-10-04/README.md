# Explicit model setup and restricted offline workflow — October 4, 2026

Both fresh-source setup schedules pass all seven checks, and both retained bundles verify offline. This covers the fresh model/runtime setup portion of B07/G14 on the supported Apple Silicon Mac; [full v1 acceptance](../../docs/v1-release-contract.md) remains pending.

| Retained evidence | Result and boundary |
|---|---|
| [Explicit download](download/report.json) | Empty artifact tree, new pinned weights/archive/license acquisition, offline asset verification/reuse and two real upload/review/export fixtures; seven checks pass |
| [Verified local transfer](transfer/report.json) | Independent asset copies into another empty tree, final verifier with timeout/interruption cleanup, offline reuse and both live fixtures; seven checks pass |
| [Final source checkout](checkout/report.json), [test log](checkout/contracts.log) | All eight offline packaging/archive checks and **375 deterministic tests** pass |
| [Initial checkout](checkout-initial/report.json) | All eight checks and 373 tests pass before two additional interruption tests; retained separately |

Each live run retains the macOS [policy](transfer/offline.sb), [parent/child denial and loopback probe](transfer/network_policy.log), [missing-asset refusal](transfer/missing_assets.log), command logs, pinned profile, complete source-file hash inventory, implementation text snapshot and nested workflow predictions/pages/review/history/exports/server log. The parser image is `sha256:09c23c3cf290412c5ae523c636c098d7f11c6a17a6a6ca6261e27419bfb6485c`, unchanged from the retained sixteen-check memory/performance parser run. Neither study changes extraction/scoring source or historical predictions.

The download run used the first verifier snapshot. Review then added separate owned process sessions and interruption/timeout cleanup, with two deterministic checks. A saved-audit implementation initially required the Dockerfile in two source snapshots; it now verifies the nested workflow snapshot and cross-binds its hashes to the original full checkout inventory. The original acquisition/runtime evidence remains unchanged. The transfer run binds the final verifier. Both saved reports verify through the current offline auditor.

Model startup was 40.358/4.605 seconds for download/transfer. Clean/conflicting upload processing was 91.559/69.954 and 81.658/57.314 seconds, respectively. **These are integration observations, not controlled performance evidence:** checkout/tests ran concurrently and filesystem/Metal caches were not cleared. Both owned native runtimes report completed shutdown, and both fresh checkout runtime scratch inventories are empty before temporary checkout removal.

The offline policy is inherited by the Python and native child processes; it allows loopback and Unix sockets. It does not disconnect the host or isolate preexisting Docker/native services. Parser containers have their own network-none policy. Host Python/Docker and an installed pinned image are prerequisites; this is not a clean-machine installation or parser rebuild. Fixture review/approval is automated and uses only the two hash-pinned fictional repository invoices. No human participation, genuine-scan quality, semantic-support assessment, peak-memory acceptance or published-tag claim is made.

Reproduce with the [setup guide](../../docs/model-setup.md). Verify the portable copies without Docker/model inference:

```sh
PYTHONPATH=src python3.12 scripts/verify_model_setup.py --verify evals/model-setup-2026-10-04/download
PYTHONPATH=src python3.12 scripts/verify_model_setup.py --verify evals/model-setup-2026-10-04/transfer
```

The checkout reports bind their respective working-source snapshots before this documentation-only handoff and evidence retention. They are not committed-tree or published-tag verification. Final-source gate confirmation, genuine scanner inputs/manual judgments, whole-application memory, comprehensive adversarial coverage and narrated presentation remain open. No commit, tag or publication is performed here.
