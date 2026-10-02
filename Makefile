PYTHON ?= python3.12
PYTHONPATH := src

.PHONY: doctor test smoke-ocr demo-baseline eval-development parser-build parser-smoke dev

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

parser-build:
	docker build -f sandbox/Dockerfile -t docwork-parser:v2 .

parser-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -p 'container_*.py' -v

dev:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m docwork.cli serve
