FROM python:3.11-slim

# ffmpeg: yt-dlp audio extraction; tzdata: CHECK_TIMES schedule in Asia/Taipei
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Taipei /etc/localtime \
    && echo Asia/Taipei > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY config/ ./config/
COPY app/ ./app/

# Environment configurations
ENV PYTHONPATH=/app
ENV TZ=Asia/Taipei
ENV YOUTUBE_API_KEY=""
ENV GEMINI_API_KEY=""
ENV TELEGRAM_BOT_TOKEN=""
ENV TELEGRAM_ADMIN_ID=""
ENV CHANNEL_IDS=""

CMD ["python", "app/main.py"]
