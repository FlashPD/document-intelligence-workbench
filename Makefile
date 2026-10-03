PYTHON ?= python3.12
PYTHONPATH := src

.PHONY: doctor test release-check evaluation-status review-browser-verify smoke-ocr demo-baseline demo-replay eval-development eval-repeatability eval-verify-corpus models-fetch models-verify parser-build parser-smoke parser-verify model-workflow-verify dev dev-model

doctor:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli doctor

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

release-check:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli release-check --output-dir $(or $(OUTPUT),artifacts/portfolio-readiness-fresh)

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
	docker build -f sandbox/Dockerfile -t docwork-parser:v3 .

parser-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'container_*.py' -v

parser-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_parser.py --output $(or $(OUTPUT),artifacts/parser-verification.json)

model-workflow-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_model_workflow.py --output-dir $(or $(OUTPUT),artifacts/model-workflow-fresh)

dev:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli serve

demo-replay:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli demo-replay $(if $(OUTPUT),--output-dir $(OUTPUT))

dev-model:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli serve --model-profile config/model-mac-instruct.json
