PYTHON ?= python3.12
PYTHONPATH := src

.PHONY: doctor test release-check release-checkout evaluation-status review-browser-verify smoke-ocr demo-baseline demo-replay eval-development eval-repeatability eval-verify-corpus models-fetch models-verify parser-build parser-smoke parser-verify model-workflow-verify dev dev-model

doctor:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli doctor

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

release-check:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli release-check --output-dir $(or $(OUTPUT),artifacts/portfolio-readiness-fresh)

release-checkout:
	$(PYTHON) scripts/verify_release_checkout.py --output-dir "$(or $(OUTPUT),artifacts/checkout-fresh)" $(if $(REF),--ref "$(REF)")

evaluation-status:
	$(PYTHON) scripts/evaluation_status.py $(if $(INSPECT_RUNNER),--inspect-runner)

review-browser-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_review_browser.py --output-dir $(or $(OUTPUT),artifacts/review-browser-fresh)

smoke-ocr:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'smoke_*.py' -v

demo-baseline:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli baseline samples/clean.png --output artifacts/clean-baseline.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli baseline samples/conflicting-total.png --output artifacts/conflicting-total-baseline.json

eval-development:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-development

eval-verify-corpus:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-verify-corpus

eval-repeatability:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-development --output artifacts/development-baseline-current.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-development --output artifacts/development-baseline-repeat.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-compare artifacts/development-baseline-current.json artifacts/development-baseline-repeat.json

models-fetch:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli models fetch

models-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli models verify

parser-build:
	$(PYTHON) scripts/verify_parser_build.py
	docker build -f sandbox/Dockerfile -t docwork-parser:v3 .

.PHONY: parser-lock-check parser-rebuild-verify
parser-lock-check:
	$(PYTHON) scripts/verify_parser_build.py

parser-rebuild-verify:
	$(PYTHON) scripts/verify_parser_build.py --output-dir "$(or $(OUTPUT),artifacts/parser-rebuild-fresh)"

parser-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'container_*.py' -v

parser-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_parser.py --output $(or $(OUTPUT),artifacts/parser-verification.json)

model-workflow-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_model_workflow.py --output-dir $(or $(OUTPUT),artifacts/model-workflow-fresh)

.PHONY: model-setup-verify
model-setup-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_model_setup.py --output-dir "$(or $(OUTPUT),artifacts/model-setup-fresh)" $(if $(ASSET_SOURCE),--asset-source "$(ASSET_SOURCE)",--download)

.PHONY: adversarial-model-verify adversarial-browser-verify
adversarial-model-verify:
	PYTHONPATH=$(PYTHONPATH) /usr/bin/sandbox-exec -f evals/model-setup-2026-10-04/transfer/offline.sb $(PYTHON) scripts/verify_adversarial.py --mode model --output-dir "$(or $(OUTPUT),artifacts/adversarial-model-fresh)"

adversarial-browser-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_adversarial.py --mode browser --output-dir "$(or $(OUTPUT),artifacts/adversarial-browser-fresh)"

dev:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli serve

demo-replay:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli demo-replay $(if $(OUTPUT),--output-dir $(OUTPUT))

dev-model:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli serve --model-profile config/model-mac-instruct.json

.PHONY: lifecycle-verify
lifecycle-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_lifecycle.py --output-dir $(or $(OUTPUT),artifacts/lifecycle-fresh)

.PHONY: access-verify
access-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_access.py --output-dir $(or $(OUTPUT),artifacts/access-fresh)

.PHONY: operations-verify
operations-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_operations.py --output-dir $(or $(OUTPUT),artifacts/operations-fresh)
