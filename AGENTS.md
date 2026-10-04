# Project continuity

Before continuing project work, read `docs/backlog.md` and `docs/v1-release-contract.md`. The backlog is the current handoff; the contract defines the full local v1 release. Read relevant implementation guides before changing a subsystem.

For each project task, update the corresponding backlog entry before finishing: status, completed work, validation evidence, remaining work, and the next concrete action. Use the user's America/Chicago date. Mark an item done only when its completion criteria are met; distinguish implementation from recorded runtime evidence. Passing the experimental portfolio audit does not complete v1 acceptance.

Keep scope decisions in `docs/adr/0001-local-v1-scope.md` consistent with the contract and backlog. Preserve original experimental predictions, pilot outcomes, and runtime reports. New work must not rewrite historical results to make them pass. Generated runtime output belongs under ignored `artifacts/`; retain reviewed evidence under `evals/` when appropriate.

Use checks appropriate to the change. Documentation-only work needs link, consistency, and diff checks; it does not require fresh inference or the full test suite. Do not commit, tag, or publish unless requested.
