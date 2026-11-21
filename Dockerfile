FROM python:3.10-slim

# Install system dependencies (FFmpeg is required for video and audio operations)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set up non-root user
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR $HOME/app

# Install Python requirements and ensure yt-dlp is always on latest release
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade yt-dlp

# Copy full application code
COPY --chown=user:user . .

# Ensure storage directories exist with write permissions
RUN mkdir -p storage/uploads storage/clips storage/downloads storage/temp

EXPOSE 10000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
