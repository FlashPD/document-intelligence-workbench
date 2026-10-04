# Pinned parser build and prior-version upgrade

Last updated: October 4, 2026, America/Chicago. This completes the build-pin and database-upgrade portions of B07/G14; full v1 remains pending under the [release contract](v1-release-contract.md).

## Explicit setup

Start Docker Desktop and run from the repository with Python 3.12:

```sh
make parser-lock-check
make parser-build
make parser-verify OUTPUT=artifacts/parser-pinned-fresh.json
```

`parser-lock-check` is offline. It checks the lock schema, hashes, permitted wheel URLs, complete English/orientation packages and agreement between the Dockerfile and locked base. It does not install packages or certify a runtime image. `parser-build` performs that check before the explicit network-enabled build. Normal uploaded-document parsing remains network-denied and uses `--pull never`; it never installs or downloads dependencies.

The [build lock](../sandbox/parser-build-lock.json) pins Python 3.12.12 through the official multi-platform image index digest, the Debian and Debian Security snapshots at `20261003T000000Z`, Poppler utilities `22.12.0-2+deb12u3`, Tesseract `5.3.0-2`, and English/orientation packages `1:4.1.0-2`. The frozen signed repositories fix transitive package selection; the base digest fixes packages already present in the base. Snapshot expiry checking is disabled only for these historical repositories. Signature and package checksum verification remain enabled. See [Docker's digest guidance](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions) and [Debian's snapshot instructions](https://snapshot.debian.org/).

The [installer](../sandbox/install_parser.py) selects the CPython 3.12 Linux arm64 or amd64 Pillow 11.3.0 wheel by exact URL and SHA-256. Pip uses hash verification, binary-only installation, no index and no dependencies; unsupported architectures fail. The [PyPI release metadata](https://pypi.org/pypi/Pillow/11.3.0/json) supplies those wheel identities. The installer checks Python/Pillow, installed direct-package versions and both trained-data hashes, then records the complete installed Debian package inventory in `/opt/docwork-build/runtime.json`. These files remain readable in the unprivileged read-only runtime container.

Only arm64 has new local runtime evidence. An amd64 wheel pin and base manifest do not establish an amd64 live pass. These are frozen release inputs, not a claim that dependencies will remain current; refreshing any pin requires a new reviewed build and applicable parser/model/performance checks.

## Two independent dependency rebuilds

```sh
make parser-rebuild-verify OUTPUT=artifacts/parser-rebuild-fresh
```

This explicitly builds two separately tagged images with `--no-cache`, rerunning Debian and Pillow installation. Base layers may be cached. Each image is probed by immutable image ID with network disabled, a read-only root, dropped capabilities, unprivileged identity and bounded resources. The verifier compares the full installed Debian version inventory, Python/Pillow, OCR bytes and copied application/build-file hashes. It rejects dependency/source drift and changes during verification, retains logs and failed attempts, and leaves the resulting local image tags available for inspection.

Passing establishes repeatable dependency selection and source identity on this host architecture. Installation timestamps and image metadata can differ, so it does not establish bit-identical OCI images or an air-gapped fresh build. Explicit setup still needs the pinned registry/archive/wheel bytes to be available; document processing is offline after setup. Runtime restrictions and input/recovery checks remain the separate parser suite.

## Upgrade from the published experimental version

Stop the server and any CLI worker before upgrading. Make and verify a portable backup first; preserve that backup independently. Install the new source and rebuild the parser, then reopen the same database and object store. Schema changes happen during store initialization. Run reconciliation before processing. Reprocessing creates a new candidate and requires fresh approval; it does not alter historical approved exports. Restore into a new directory if rollback/recovery is needed. This workflow does not support concurrent old and new application versions against one database.

Run the reproducible offline upgrade probe:

```sh
PYTHONPATH=src python3.12 scripts/verify_upgrade.py \
  --output-dir artifacts/upgrade-fresh
```

The repository must contain `v0.1.0-experimental` at commit `6c2b3c524baee68e8598408e415bdc7a043f3d73`. The probe reads that committed tree directly, runs its code in a separate Python process to create a disposable database, then opens it using current code. Four fictional recorded development candidates include two edited/acknowledged/approved fixture records with JSON/CSV exports and two unapproved records. Two additional uploads supply one active and one queued legacy job. Fixture approvals are automated persistence checks, separate from human studies.

Assertions verify historical records, OCR pages, issues, decisions, event history, approvals and export hashes; original checksums; rules defaults for legacy jobs; SQLite integrity/foreign keys; idempotent reopening; portable backup and relocated restore; restored active-worker fencing; and approval invalidation after a new edit. All temporary databases are removed. The retained expected state, source binding and report document the observation; they are not a signed attestation or a measurement of extraction quality.

## Recorded evidence and remaining gates

The [October 4 reproducibility bundle](../evals/reproducibility-2026-10-04/README.md) retains two passing uncached rebuilds, all sixteen live parser checks, the prior-version upgrade probe, deterministic tests, pinned-image rules timing and applicable model/checkout evidence. Failed earlier build/probe attempts remain separate.

The [fresh-checkout model setup verifier](model-setup.md) now has [retained passing evidence](../evals/model-setup-2026-10-04/README.md) for explicit pinned acquisition and verified local transfer followed by a network-restricted real workflow. This covers the remaining setup portion of G14 on the recorded working-source configuration; final-release source/tag confirmation remains B07. The [later memory/performance study](../evals/memory-performance-2026-10-04/README.md) completes both schedules on the source-refresh pinned image. Whole-application memory accounting, genuine scanner inputs/manual source assessments, final adversarial coverage and the full release presentation remain separate work. Passing these checks does not close all fifteen v1 gates or authorize publication.
