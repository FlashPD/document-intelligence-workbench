# Continuous narrated workflow demonstration

The [recorder](../scripts/record_narrated_demo.py) demonstrates a fresh browser upload through real rules parsing, cited source/rotation inspection, versioned correction, unapproved-export refusal, approval, byte-verified JSON/CSV downloads, service restart and checkpoint reprocessing. It uses a disposable workbench and a fictional clean fixture. The voice is synthesized by the installed macOS speech utility; actions and fixture approvals are scripted. It supplies presentation/runtime evidence, not human-review outcomes or semantic/scan quality.

The [retained current-source recording](../evals/narrated-demo-2026-10-04/README.md) passes seven workflow checks and nine narration scenes, with 371 viewport observations, zero capture errors and a 0.443-second maximum gap. Capture/finalization lasts 130.214 seconds; this is presentation pacing, not processing latency or human effort. The final screenshot shows revision 4 requiring approval after reprocessing, with legible source/citation fields and no storage warning.

Earlier attempts preserve cleanup, background-tab polling and playback failures separately. Bringing the application/encoder to the foreground resolves their background throttling; owned helper shutdown is verified before scratch removal. Visual review also exposed a [storage-inventory race](../evals/storage-inventory-2026-10-04/README.md), now fixed and covered by new native checks. The passing pre-fix recording remains historical with source drift.

Execute fresh recording **after controlled benchmarks finish**, on the reference Mac with Python 3.12, Docker, the pinned parser image, installed Chrome and a locally available macOS voice. It uses the existing parser image and rules; it does not acquire or start a model. Do not run builds, test suites or browser recording during controlled timing.

```sh
PYTHONPATH=src python3.12 scripts/record_narrated_demo.py \
  --output-dir artifacts/narrated-demo-new
PYTHONPATH=src python3.12 scripts/record_narrated_demo.py --verify \
  --output-dir artifacts/narrated-demo-new
```

Choose a new output directory. Optional `--voice` selects an already-installed voice. A failed synthesis, browser, assertion, media playback or capture produces a failed report and preserves available output; use a new directory after fixing the cause. The recorder owns its Chrome process group, connections, service/supervisor and temporary database/object store. It stops processes and verifies non-zombie helper exit before removing scratch, preserving the primary failure and cleanup status. Four focused tests cover incomplete/cut/error schedules, waveform silence/metadata, archive omissions and real owned parent/child termination; process observation is injected for restrictive host sandboxes.

The viewport is sampled continuously with a quarter-second wait after capture/transfer. A second blank Chrome page runs canvas/MediaRecorder and mixes synthesized PCM through Web Audio into WebM video/audio. Frames are drawn at wall rate; no screenshot sequence is edited into a montage. Native inspection/capture/encoding takes time, so frame timestamps, query errors and actual gaps remain explicit. Completion requires all nine narration scenes, zero capture errors, at least thirty frames, a maximum two-second frame gap and observed start/end/scene windows. This sampling is not a claim that every rendered frame or input movement is captured.

The produced video must decode/play with both video and audio bytes reported by Chrome. Source snapshots, original fixture, WAV clips, transcript, final screenshot, recording timeline, verified export hashes and review-state hashes accompany the video. Restart is a clean service stop/reopen with persisted committed state; [stage drills](stage-recovery.md) independently exercise controlled process crashes. Reprocessing uses a verified checkpoint, preserves the earlier export and requires new approval.

Saved verification needs Python only. It audits the source snapshot, inventory, WAV format/non-silence, narration schedule, reported capture/check/cleanup outcomes and WebM signature. It does not independently decode the video, rerun Chrome/OCR or establish human participation; the live producer's reported playback test has that boundary. Source drift remains disclosed. The separate retained Chrome playback review decodes the full 127.080-second audio and seeks source/approval/reprocessing frames from the actual saved file. It does not establish perceptual speech accuracy or human participation. Local hash-bound reports are not independent attestations.

Review the actual video and screenshot for legibility and audio alignment before retaining it in `evals/`. A passing recorder does not close G15's final gate/source/commit/tag/clone requirements or any missing production-input/memory acceptance. The [case study](engineering-case-study.md), [release worksheet](release-readiness.md), [contract](v1-release-contract.md) and [backlog](backlog.md) remain the complete release context.
