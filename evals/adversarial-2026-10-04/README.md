# Adversarial boundary evidence — October 4, 2026

Reviewed fictional-fixture observations for G08. See the [runbook](../../docs/adversarial.md) for methods, commands and limits. No product extraction, parser, review, UI or export implementation changed in this task.

| Evidence | Result and scope |
|---|---|
| [Initial real model](model-initial/report.json) | Seven checks; both hostile PDFs parsed by the pinned container and extracted by pinned Qwen3 4B/llama.cpp on Apple M1 under the inherited offline policy. Each original total remains `270.00`; row errors remain visible. No review authority or exports. |
| [Model applicability](model-applicability.json) | Application/UI, parser build, model profile and attack inputs match current source. Verifier/test differences and later fixture documentation are explicit. Final-verifier model refresh did not execute after two automatic approval-review timeouts. |
| [Refreshed Chrome](browser/report.json) | Nine checks; literal hostile markup, formula-safe authenticated downloads, current approval and invalidation, preserved historical bytes and owned shutdown. [Screenshot](browser/literal-values.png) visually reviewed. Saved development OCR and automated fixture approvals only. |
| [Initial Chrome](browser-initial/report.json) | Nine checks; original source snapshot preserved and used by corruption tests. |
| [Access](access/report.json) | Ten real loopback HTTP checks for established actor, forged identities, model/processing credentials, session/origin rules and download integrity. |
| [Parser](parser/report.json) | Eighteen pinned-image checks including independently validated encrypted fixtures, refusal without partial publication, existing runtime/resource/recovery/restore controls. |
| [First parser failure](parser-first-failed/report.json), [log](parser-first-failed/run.log) | One of eighteen failed: existing two-second-lease abrupt-exit probe lost ownership before its callback. Root cause unresolved; passed alone and on sequential full rerun without implementation/harness changes. Original failure preserved. |
| [Isolated source checkout](checkout/report.json) | All eight checks and 384 deterministic tests pass; no runtime assets copied, inference or downloads. Source snapshot precedes documentation-only evidence/handoff finalization; not a committed/tagged or published release. |

The parser identity is `sha256:09c23c3cf290412c5ae523c636c098d7f11c6a17a6a6ca6261e27419bfb6485c`. Tests verify the current source inside that image. Encrypted cases use legacy RC4-128 with empty/nonempty fictional passwords; production returns `PDF_ENCRYPTED`/`PDF_INFO_FAILED`, respectively. Fixture generation uses separately installed pypdf 6.1.1; no production/test dependency is added.

The nine new deterministic tests comprise three behavioral adversarial scenarios and six saved-evidence integrity tests. Malformed authority output fails after two injected requests; schema-valid wrong values remain unapproved and flagged. CSV checks cover eight formula prefixes/whitespace combinations and preserve valid negative numeric cells. Archive tests reject omitted cases, false runtime/model identity, candidate/prompt substitution, review events, executable DOM markers, unescaped CSV, stale approval, tampering, extra files and symlinks.

```sh
PYTHONPATH=src python3.12 scripts/verify_adversarial.py --verify evals/adversarial-2026-10-04/model-initial
PYTHONPATH=src python3.12 scripts/verify_adversarial.py --verify evals/adversarial-2026-10-04/browser-initial
PYTHONPATH=src python3.12 scripts/verify_adversarial.py --verify evals/adversarial-2026-10-04/browser
```

These audits verify retained integrity and reproduce bounded extraction outcomes from recorded responses; they do not rerun inference or cryptographically attest the machine. The controls limit authority even when model extraction is wrong. This bundle makes no general attack-resistance, genuine-scan quality, human productivity, latency, memory or complete-v1 claim. Remaining work is in the [backlog](../../docs/backlog.md) and [release contract](../../docs/v1-release-contract.md).
