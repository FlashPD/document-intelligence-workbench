FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY sandbox/parser-build-lock.json sandbox/install_parser.py /opt/docwork-build/
RUN python /opt/docwork-build/install_parser.py
COPY src/docwork /app/docwork
ENV PYTHONPATH=/app
WORKDIR /app
ENTRYPOINT ["python", "-m", "docwork.parser_entry"]
