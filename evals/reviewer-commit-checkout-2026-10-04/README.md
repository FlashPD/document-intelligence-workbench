# Exact reviewer/CI commit verification

The [report](report.json) verifies commit `d9f4e678b53c5eb9a5a3b29299a0ea560ed28165`, tree `61ad6287a7e10b3b1864a22ae3b3084e581fb85d`. All eight isolated offline commands passed, including [335 deterministic tests](contracts.log) and the refreshed post-pilot correction check. Inputs were copied from Git blobs, with no local model, runtime, workbench or receipt-source assets.

```sh
make release-checkout REF=d9f4e67 OUTPUT=artifacts/reviewer-commit-check-fresh
```

Use a new output directory. This verifies the committed reviewer/CI repair, not the subsequent B04/B05 working source, hosted GitHub execution, genuine scan/model inference, human participation or full v1 acceptance. Source identities, all command results and log hashes remain in the report.
