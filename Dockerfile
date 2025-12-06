FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV and graphics
RUN apt-get update && apt-get install -y \
    build-essential \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libxext6 \
    libsm6 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose port
EXPOSE 8000

# Use shell form to allow environment variable substitution
CMD sh -c "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"
