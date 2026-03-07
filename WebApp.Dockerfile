# Start from the official Python 3.11 image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the requirements file first and install dependencies
# This leverages Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire backend application code
COPY app.py .
COPY backend/ ./backend/
COPY templates/ ./templates/
COPY static/ ./static/

# Create the static UI directory and copy frontend files (alternate UI)
RUN mkdir -p /usr/src/app/static/ui
COPY frontend/index.html /usr/src/app/static/ui/
COPY frontend/script.js /usr/src/app/static/ui/
COPY frontend/style.css /usr/src/app/static/ui/

# Expose the port the app runs on (matches WEBSITES_PORT)
EXPOSE 8000

# Define the command to run the app
CMD ["gunicorn", "--workers", "3", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "app:app"]
