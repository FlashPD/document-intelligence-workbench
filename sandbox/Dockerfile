FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir Pillow==11.3.0
COPY src/docwork /app/docwork
ENV PYTHONPATH=/app
WORKDIR /app
ENTRYPOINT ["python", "-m", "docwork.parser_entry"]
