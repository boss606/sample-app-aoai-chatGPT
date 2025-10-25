# Start from the Python base image
FROM python:3.11-alpine

# Install system dependencies needed by some Python packages
# Keeping the original dependencies just in case
RUN apk add --no-cache --virtual .build-deps \
    build-base \
    libffi-dev \
    openssl-dev \
    curl \
    && apk add --no-cache \
    libpq

# Set the working directory
WORKDIR /usr/src/app

# Copy and install Python requirements first (for better caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache

# Copy the Python backend code and necessary files
COPY ./backend ./backend
COPY app.py .
COPY gunicorn.conf.py .

# Copy the original static assets (like favicon, images if any)
# These will be available at /static/ on the web server
COPY ./static ./static

# Copy your new frontend files (index.html, index.css/style.css, script.js)
# into a subfolder within the static directory.
# These will be available at /static/ui/ on the web server.
# The backend might need configuration to serve index.html from here as the root.
COPY ./frontend ./static/ui

# Expose the port Gunicorn will run on
EXPOSE 80

# Command to run the Python backend application using Gunicorn
# This command remains the same as it starts your Python API backend
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
# Note: Changed from "-b 0.0.0.0:80" to use the config file like the original repo likely did.
# If gunicorn.conf.py doesn't exist or isn't right, use:
# CMD ["gunicorn", "-b", "0.0.0.0:80", "app:app"]
