#!/usr/bin/env bash
set -euo pipefail

cd /home/site/wwwroot
export PYTHONUNBUFFERED=1

# Continuous WebJob runner:
# consumes Azure Storage Queue and runs OCR+embeddings in process_attachment_job().
python -u worker_attachment_queue.py

