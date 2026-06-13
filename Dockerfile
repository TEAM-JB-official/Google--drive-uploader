FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create downloads directory
RUN mkdir -p downloads

# Run both FastAPI and bot
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port 8000 & python bot.py"]
