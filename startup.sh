#!/usr/bin/env bash
set -e

# Install system dependencies needed for OCR (poppler provides pdfinfo/pdftoppm)
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  poppler-utils tesseract-ocr
rm -rf /var/lib/apt/lists/*

# Attachment queue worker: when WebJob/SSH is unavailable, run the consumer in-process.
# Set ENABLE_ATTACHMENT_QUEUE_WORKER=0 in App Settings if a dedicated worker runs elsewhere.
if [ "${ENABLE_ATTACHMENT_QUEUE_WORKER:-1}" != "0" ]; then
  python -u worker_attachment_queue.py &
fi

# Start the application
gunicorn --bind=0.0.0.0:8000 --workers=2 -k uvicorn.workers.UvicornWorker app:app
