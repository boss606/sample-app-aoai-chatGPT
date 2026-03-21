#!/usr/bin/env bash
set -e

# Install system dependencies needed for OCR (poppler provides pdfinfo/pdftoppm)
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  poppler-utils tesseract-ocr
rm -rf /var/lib/apt/lists/*

# Start the application
gunicorn --bind=0.0.0.0:8000 --workers=2 -k uvicorn.workers.UvicornWorker app:app
