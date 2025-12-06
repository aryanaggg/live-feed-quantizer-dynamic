FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV
RUN apt-get update && apt-get install -y \
    build-essential \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose port
EXPOSE 8000

# Run the app
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
