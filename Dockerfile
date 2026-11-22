FROM python:3.10-slim

# Copy official Deno binary for yt-dlp JavaScript challenge solver
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Install system dependencies (FFmpeg for video/audio, Node.js for yt-dlp challenges)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements cleanly as root and upgrade yt-dlp to latest
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -U yt-dlp

# Copy application files
COPY . .

# Ensure storage directories exist with full write permissions
RUN mkdir -p storage/uploads storage/clips storage/downloads storage/temp && \
    chmod -R 777 storage

ENV PYTHONUNBUFFERED=1 \
    PORT=10000

EXPOSE 10000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
