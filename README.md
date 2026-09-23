# Local Document Intelligence & Review Workbench

**Status: architecture planned; implementation has not started.** Build after the first Secure Agent Platform portfolio release.

A local invoice-processing workbench that extracts structured fields and line items, links them to page evidence, flags uncertain results, and exports records after human review.

[Architecture and implementation plan](arch_plan/document-intelligence-workbench-plan.md)

The Mac hosts the application, documents, review UI, and default processing pipeline. An optional RTX 5080 PC accelerates local inference. No paid cloud service or model API is required.

The plan includes separate synthetic invoice and real receipt evaluations, a conventional OCR baseline, an optional document vision-model comparison, and a reproducible portfolio demo. Proposed commands and targets are not implemented or measured yet.
