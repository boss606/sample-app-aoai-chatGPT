#!/usr/bin/env bash
set -e

# Install system dependencies needed for OCR (poppler provides pdfinfo/pdftoppm)
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  poppler-utils tesseract-ocr
rm -rf /var/lib/apt/lists/*

# Manage Python venv outside wwwroot so it survives --clean deploys
# Oryx is disabled (SCM_DO_BUILD_DURING_DEPLOYMENT=false), so we handle deps here
VENV=/home/.venv
if [ ! -d "$VENV" ]; then
  python -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet -r /home/site/wwwroot/requirements.txt

# Start the application
"$VENV/bin/gunicorn" --bind=0.0.0.0:8000 --workers=2 -k uvicorn.workers.UvicornWorker app:app
