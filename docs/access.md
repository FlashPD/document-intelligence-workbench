# Local reviewer authority

B03 establishes reviewer identity on the server for the supported single-user Mac/Linux boundary. The [v1 contract](v1-release-contract.md) and [backlog](backlog.md) track final acceptance separately.

Start `make dev`, `make dev-model` or `make demo-replay`, then open the printed session URL. **Signed in as** displays `local:OS_ACCOUNT`, derived from the server process's account through the OS account database. Changing `USER`/`LOGNAME`, editing the display, submitting a name or writing instructions into an invoice cannot select a reviewer. Possession of the ephemeral startup/session capability delegates access as that account; it does not independently identify the person at the keyboard.

Edits, issue acknowledgments, approvals and pilot trial starts obtain their actor from the authenticated server principal. Clients should omit `actor`. Older clients may echo the exact established actor; any different value receives `403` before a review mutation. Existing historical labels and approval/export hashes remain unchanged. Current-revision, blocking-issue and stale-edit checks still apply after authorization.

| Capability | Transport | Allowed operations |
|---|---|---|
| Reviewer | HTTP-only `docwork_session` cookie from the startup URL | Browser, document/page/history reads, intake/lifecycle controls, review and approved export creation/download |
| Processor | Separate `Authorization: Bearer …` capability | Only `POST /api/batches`, `/api/upload`, `/api/process-one`; replay/pilot restrictions still apply |
| Model/parser | Model runtime key or parser job input | No workbench HTTP authority; output remains candidate data |

Every mutation requires the exact server origin. A processing Authorization header takes precedence over any reviewer cookie; combining credentials cannot raise privileges. Unknown credentials return `401`, and valid capabilities outside their permission return `403`. Page images and all JSON/CSV downloads require reviewer permission. Random session/processing capabilities live only in server memory and change on restart; they are absent from records, job profiles, runtime responses and default logs.

The normal worker runs inside the trusted host process and needs no HTTP credential. `ReviewServer.access.processing_token` exists for trusted in-process integrations and verification; it is never printed, given to extraction, or exposed in the browser. There is no external token-issuance or multi-user account-management feature. Parser containers receive neither the database nor reviewer/model credentials. In-process Python and the OS account remain trusted; HTTP capabilities do not sandbox arbitrary host code.

## Trusted CLI boundary

The CLI and direct Python store methods operate as a trusted local operator with database/filesystem access. Their `--actor` strings remain audit labels, not HTTP credentials or proof of human identity. An operator with database access can already alter records or invoke review methods. Do not provision database access to an untrusted extractor. This release does not claim enterprise identity, multi-user isolation, independent proof of human participation, or invoice authenticity.

## Verify

```sh
make access-verify OUTPUT=artifacts/access-fresh
PYTHONPATH=src python3.12 scripts/verify_review_browser.py \
  --skip-recording --demo-replay --output-dir artifacts/access-browser-fresh
PYTHONPATH=src python3.12 scripts/verify_pilot_browser.py \
  --output-dir artifacts/access-pilot-fresh
make test
```

Choose new output paths. The HTTP check uses disposable recorded fictional candidates, real loopback transport, forged actors, processing/model credentials, stale approvals, same-origin enforcement and checksum-verified downloads. Chrome checks require installed Chrome and cover replay/pilot controls and the read-only identity display. They run no live model, fresh OCR, human trial or productivity measurement. Reviewed evidence is retained under [evals/access-2026-10-04](../evals/access-2026-10-04/README.md).
