# Dockerfile for Agent 1 Research Ingestion Agent

# Use a minimal Python image
FROM python:3.10-slim

# Install system dependencies for audio/document processing
RUN apt-get update && \
    apt-get install -y ffmpeg libsndfile1 build-essential && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements.txt and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Download SpaCy models
RUN python -m spacy download en_core_web_sm

# Create output directories
RUN mkdir -p /app/output /app/input

# Expose ports if needed (change the port as needed)
EXPOSE 8080

# Health check (adjust the command as needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Entry point to run the agent
CMD ["python", "agent.py"]
