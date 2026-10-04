# Production-tooling checkout verification

The [report](report.json) records all eight isolated offline checks passing, including [347 tests](contracts.log). Its source inventory binds the current implementation, reviewed diagnostic evidence and documentation before the later documentation-only handoff/result-retention update. It is a working-source snapshot, not a committed tree, hosted Actions result or full v1 acceptance.

```sh
make release-checkout OUTPUT=artifacts/production-tooling-checkout-fresh
```

Use a new output path. No local workbench, model weights or runtime assets were copied. See the [diagnostic evidence](../production-tooling-2026-10-04/README.md) for actual parser/model runs and their limits.
