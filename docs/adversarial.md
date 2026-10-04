# Bounded adversarial verification — October 4, 2026

The [retained evidence](../evals/adversarial-2026-10-04/README.md) exercises encrypted PDFs, instructions embedded in actual parsed invoices, malicious browser values, spreadsheet formulas and review credentials. It adds coverage for G08; it does not certify general prompt-injection resistance or complete v1 acceptance.

| Boundary | Verification and outcome |
|---|---|
| Valid encrypted originals | Pinned container `pdfinfo` independently opens each fictional fixture with its password and confirms encryption and one page. Production processing refuses both: empty-password encryption produces `PDF_ENCRYPTED`; a required password produces `PDF_INFO_FAILED`. Neither publishes pages, a parser checkpoint or a candidate; quarantine is empty. |
| Model-directed document instructions | Two complete fictional PDFs demand approval/export, privileged actors, tools, changed totals and credential disclosure. Real Docker OCR feeds the unchanged system/schema contract; hostile text stays in the user data. Both complete one real model request with original total `270.00`, unapproved revision 1, no decisions, approvals, exports or edits. Row errors remain reported: `INVALID_ROW_AMOUNT` and `ROW_ARITHMETIC_MISMATCH`. |
| Review authority | Real HTTP requests using model or processing credentials, even with a reviewer cookie, cannot edit, acknowledge, approve or export. A separate ten-check access refresh covers established identity, forged actors, origins and authenticated downloads. |
| Hostile model output | Injected deterministic responses adding `approval`, `actor`, `tools` or `revision` fail after the fixed two-request allowance. A schema-valid zero total with invented evidence stays an unapproved candidate with `EVIDENCE_UNKNOWN` and `TOTAL_MISMATCH`; approval is blocked. These are injected outputs, not additional real inference. |
| Browser markup | Owned headless Chrome checks literal filename, header, row, history and issue-detail text; no injected node appears and the execution marker remains zero. Values are controlled fixture edits; the issue detail is supplied directly to the diagnostic renderer. The screenshot is visually inspected. |
| CSV and approval | Eight deterministic text cases cover `=`, `+`, `-`, `@` and leading spaces/tab/CR/LF. Text receives an apostrophe; JSON keeps original text and valid negative numeric amounts stay numeric. Chrome/HTTP verifies a formula in both header and row exports, current server-account approval, subsequent approval invalidation and unchanged historical export bytes. |
| Existing parser/storage controls | The eighteen-check live parser suite retains malformed/over-page/truncated input, runtime network/capability restrictions, timeout/OOM cleanup, retry, checkpoint fencing and portable restoration. Existing deterministic tests retain size/path/symlink, cancellation, quotas and stale authority cases. |

The PDFs are [self-authored fixtures](../tests/fixtures/security/README.md), not scanner captures or held-out data. The generator needs separately installed `pypdf==6.1.1`; production parsing, deterministic tests and archive audits do not. Its legacy RC4 algorithm is only a refusal fixture.

## Reproduce

Use Python 3.12 and the installed pinned parser from the [build guide](parser-build.md). Live model mode also needs the cached assets/runtime from [model setup](model-setup.md); the target inherits the retained macOS offline policy and checks parent/child outbound denial before starting its owned server. The separate Docker daemon is outside that host process policy; parser containers have network mode `none`.

```sh
make test
make parser-verify OUTPUT=artifacts/adversarial-parser-fresh/report.json
make adversarial-model-verify OUTPUT=artifacts/adversarial-model-fresh
make adversarial-browser-verify OUTPUT=artifacts/adversarial-browser-fresh
PYTHONPATH=src python3.12 scripts/verify_access.py --output-dir artifacts/adversarial-access-fresh
make release-checkout OUTPUT=artifacts/adversarial-checkout-fresh
```

Browser mode uses installed Google Chrome, replayed development OCR and disposable stores. All model/browser outputs must use a new nonsymlink directory under ignored `artifacts/`. Model mode never approves; browser and deterministic approvals are automated fixture operations. Reports record owned-runtime shutdown and immutable source/input identities.

Saved checks run without live inference, Chrome, Docker or pypdf:

```sh
PYTHONPATH=src python3.12 scripts/verify_adversarial.py --verify evals/adversarial-2026-10-04/model-initial
PYTHONPATH=src python3.12 scripts/verify_adversarial.py --verify evals/adversarial-2026-10-04/browser
```

The auditor checks exact file inventories, hashes, snapshot identities, complete case order, model pins, authority history and export contents. It reconstructs successful model records and validation issues from the saved OCR/request/response sequence; rehashed substitute candidates/prompts and omitted cases cannot pass. Deliberate archive-corruption tests also exercise false execution markers, unescaped CSV, stale approval, extra files and symlinks. These are inspectable local observations, not signed attestations.

## Preserved limitations and failures

The first eighteen-check parser run had one failure in the pre-existing two-second-lease abrupt-exit test. The claim expired before its exit callback; the root cause was not established. That test passed in isolation, then all eighteen checks passed in the sequential rerun without changing product or harness code. The [failed report](../evals/adversarial-2026-10-04/parser-first-failed/report.json) and log remain alongside the passing run. This leaves a short-lease test stability concern and does not establish recovery at every processing stage or after power loss.

The first real-model run passed seven checks. Later verifier-only corrections normalized tuples for JSON reconstruction, fixed the review-event name to `field_edited`, and strengthened saved-evidence validation. The initial runtime report and snapshot remain unchanged. All application/UI modules, parser build, model profile and fixture bytes still match that run; [applicability](../evals/adversarial-2026-10-04/model-applicability.json) identifies the two changed verifier/test files and later fixture README. A refreshed model run was not executed because automatic permission approval review timed out on the initial request and its single allowed retry. Current offline reconstruction passes, but this is not a new live run of the final verifier.

Final committed/tagged-source confirmation remains B07. Genuine scanner inputs, manual semantic and approved-quality judgments, whole-application memory accounting, remaining stage-recovery drills and the narrated presentation remain governed by the [release contract](v1-release-contract.md).
