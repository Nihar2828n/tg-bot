# Render-compatible Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system deps (for any compiled packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .

# Render sets PORT env var, but polling mode doesn't need a web server
# We just run the bot directly
CMD ["python", "bot.py"]