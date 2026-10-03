# Browser workflow verification and scripted demo

The [recorded browser bundle](../evals/review-browser-2026-10-03-v2/report.json) checks four fictional development invoices in real Chrome: a clean page, printed total conflict, sideways page, and two-page PDF preview. The [playable demonstration](../evals/review-browser-2026-10-03-v2/index.html) includes a captioned WebM and five original screenshots. It replays hash-verified OCR/rules suggestions in a disposable workbench, with the reviewer explicitly labeled `scripted-fictional-demo`.

This is automated interface and presentation evidence. It contains no live parsing, model inference, human timing, or productivity measurement. The video assembles three-second holds of browser screenshots with captions; its duration is presentation pacing, not processing latency. It has no audio. The temporary store, exports, and Chrome profile are removed after verification. It cannot attach to an existing user's workbench.

## Verified behavior

| Area | Check |
|---|---|
| Provenance | Replayed candidates display **RECORDED OCR RULES · REPLAY** outside the timed author pilot |
| Keyboard | Arrow keys move both field selection and focus; Enter opens correction. Native action-button Enter still activates the action |
| Source visibility | On desktop, the source panel stays beside the fields while correcting long records; it returns to normal flow in the narrow layout |
| Page selector | Trusted arrow/Enter events remain uncanceled and preserve field/focus; its change handler displays page 2 |
| Revision control | A demonstration edit creates revision 2 and preserves revision 1; a stale revision edit is rejected; restoring the original creates revision 3 |
| Approval/export | Unapproved export and unresolved-conflict approval are blocked; approval belongs to revision 3; JSON and both CSV downloads match stored bytes |
| Geometry | Cited highlight bounds match the independent Python transform, and sampled nonwhite raster pixels match at 0°, 90°, 180°, and 270° |
| Multiple pages | Selecting a row cited on page 2 navigates there; changing pages retains each page's separate review rotation |
| Missing evidence | A missing row-tax value displays no cited line rather than a highlight |
| Layout | The page fits a 390-pixel viewport without horizontal document overflow |

The geometry checks use the saved OCR line rectangles, not exact field-level semantic evidence. Twelve sampled source pixels are checked per tested rotation, rather than every pixel. The highlight tolerance is 0.004 of displayed page dimensions. The page-selector test checks trusted uncanceled key events and dispatches the choice separately; operating-system popup-menu navigation is not verified. Narrow viewport checks establish the stated layout condition, not a mobile accessibility audit.

The browser check found and fixed shortcuts that intercepted unrelated button/select keys. It also corrected the author-pilot label previously shown for every replay and changed the editor label from “Approved value” to “Corrected value.” The [separate refreshed pilot check](../evals/review-pilot-browser-2026-10-03-v5/report.json) retains pause/resume, source gating, approval, export, and completion coverage in timed mode. Neither automation report can substitute for human pilot outcomes.

## Reproduce

Use Python 3.12, a repository checkout with committed corpus/evidence, and an installed Chrome executable. No Docker, Tesseract, model weights, ffmpeg, node, or download is needed:

```sh
make review-browser-verify OUTPUT=artifacts/review-browser-fresh
```

The default executable is the installed macOS Chrome path. For another host:

```sh
PYTHONPATH=src python3.12 scripts/verify_review_browser.py \
  --chrome /path/to/chrome --output-dir artifacts/review-browser-other-host
```

That host/profile requires its own passing report. `--skip-recording` runs the controls and captures screenshots without WebM encoding. It does not invent a recording. A failed check or encoding/decoding failure cannot publish a passing report. Existing output directories are rejected.

`release-check` validates the artifact inventory, saved source snapshot, declared cases, check coverage, frame list, and recorded playback metadata. Source changes mark historical evidence pending rather than silently certifying a different build. Hashes are local integrity checks, not signed attestation. Video playback was checked in Chrome; playback in other media players and a human review of the presentation remain separate checks.

```sh
PYTHONPATH=src python3.12 -m docwork.cli release-check \
  --output-dir artifacts/review-release-audit-fresh \
  --demo-recording evals/review-browser-2026-10-03-v2/demo.webm
```

The browser default selects the new bundle. Supply `--browser-directory` to audit a later run. This recording check can pass while extraction comparisons and human timing remain pending.
