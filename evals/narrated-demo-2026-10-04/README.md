# Continuous narrated live workflow — October 4, 2026

The [standalone video/player and transcript](final/index.html) show a continuous live Chrome workflow on a fictional invoice with real Docker rules parsing, scripted controls and synthesized macOS narration. The [source-bound report](final/report.json) records **seven workflow checks**, **nine narration scenes**, **371 viewport observations**, **zero query errors** and a **0.443-second maximum frame gap**. Capture/verification lasts **130.214 s**, including media finalization; that duration is presentation pacing, not processing latency or human effort. The WebM is 2,766,984 bytes at 1440×1160 with VP9/Opus. The producer reports decoding/playback of audio and video. A separate [local-file playback review](playback-review/report.json) seeks three scenes and decodes the complete encoded audio: **127.080 seconds**, stereo 44.1 kHz, sampled peak 0.876. Its source/approval/reprocessing frames were visually inspected for legibility and coherent revisions; owned Chrome/server shutdown passes.

The current parser is **`sha256:93332a57ebdc02c1368925eb02cf6de2ae85260f845f1894be3814ea0f157b27`**. Fresh upload, cited source/rotation, versioned edits/unapproved-export refusal, revision approval, byte-verified JSON/CSV, service restart and checkpoint reprocessing all pass. Restart preserves committed edits/approval/export bytes; reprocessing preserves earlier exports and requires fresh approval. A final assertion rejects the spurious storage warning found during earlier visual review. The [guard fix and fresh 18-parser/12-stage checks](../storage-inventory-2026-10-04/README.md) have separate evidence.

The [final screenshot](final/final.png) was inspected for legible source/citation fields, revision 4, reprocessing status and absence of the earlier storage warning. The [saved audit](offline-verification.json) verifies **current source**, artifact hashes, complete audio/scene/window schedule, reported workflow/cleanup outcomes and original fixture bytes. Chrome helpers, service/supervisor and scratch stop/remove cleanly. No source database, session credential, model asset or Chrome profile is retained.

## Preserved attempts

| Attempt | Outcome |
|---|---|
| [Initial](initial/report.json) | Temporary-directory removal raced owned writers and obscured a preceding failure. Original cause is unavailable; the leftover owned scratch was removed after process shutdown. |
| [Foreground failure](foreground-failure/report.json) | Primary upload/UI-settle assertion retained, with complete cleanup. An owned-page diagnostic observed `document.hidden=true`: the encoder tab had paused workbench polling. |
| [Playback failure](playback-failure/report.json) | All seven workflow checks complete, then media verification times out. The playback tab was backgrounded; the following preflight validates bringing it to the foreground. Final screenshot also exposed the storage-inventory warning. |
| [Media-only preflight](media-preflight/report.json) | Synthetic one-second tone/owned blank Chrome page prove audio/video encoding and playback with clean owned-session exit. This is not workflow/human evidence. |
| [Pre-storage recording](pre-storage/report.json) | Complete seven-check/nine-scene recording before the guard fix; retained video/player. Saved audit reports source drift. |
| [Final](final/report.json) | Current guard/parser image, complete recording, no spurious storage warning, verified audio/video and cleanup. |

Producer logs are siblings of the final/positive evidence directories so they do not alter those strict inventories. Earlier failures retain their own sources, original fixture, generated WAV files and available screenshots. No report is rewritten to make an earlier attempt pass. Separate playback attempts initially selected Chrome’s extension background target: [first failure](target-failure-1/report.json), [second failure](target-failure-2/report.json), [diagnostic navigation](target-diagnostic/report.json). Selecting the actual owned page and explicitly starting the metadata-preload player produces the passing review; these probe failures do not rewrite producer outcomes.

## Reproduce/audit

```sh
PYTHONPATH=src python3.12 scripts/record_narrated_demo.py --verify \
  --output-dir evals/narrated-demo-2026-10-04/final
make narrated-demo OUTPUT=artifacts/narrated-demo-new
```

Run fresh recording after controlled timing, using installed Chrome/speech, Docker and the current pinned parser. It does not start/acquire a model. The [runbook](../../docs/narrated-demo.md) explains native capture/encoding and failed-run cleanup. Saved verification needs Python only and does not independently decode the media or rerun the live assertions; reported playback belongs to the producer's actual Chrome run. Viewport sampling does not claim every rendered frame/input movement is captured.

Scripted fixture approvals and synthesized narration supply no human quality, semantic audit, physical scan, productivity, controlled timing or real-model evidence. This covers the declared narrated-presentation portion of G15, with final source/gate/commit/tag/clone checks still pending. The [case study](../../docs/engineering-case-study.md), [gate worksheet](../../docs/release-readiness.md), [contract](../../docs/v1-release-contract.md) and [backlog](../../docs/backlog.md) preserve those limits.
