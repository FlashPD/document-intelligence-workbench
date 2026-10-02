PYTHON ?= python3.12
PYTHONPATH := src

.PHONY: doctor test smoke-ocr demo-baseline eval-development eval-repeatability models-fetch models-verify parser-build parser-smoke dev

doctor:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli doctor

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

smoke-ocr:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'smoke_*.py' -v

demo-baseline:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli baseline samples/clean.png --output artifacts/clean-baseline.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli baseline samples/conflicting-total.png --output artifacts/conflicting-total-baseline.json

eval-development:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-development

eval-repeatability:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-development --output artifacts/development-baseline-current.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-development --output artifacts/development-baseline-repeat.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli eval-compare artifacts/development-baseline-current.json artifacts/development-baseline-repeat.json

models-fetch:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli models fetch

models-verify:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli models verify

parser-build:
	docker build -f sandbox/Dockerfile -t docwork-parser:v2 .

parser-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'container_*.py' -v

dev:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli serve
