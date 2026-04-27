FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Playwright + supervisord
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency file and install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Install Playwright browsers
RUN playwright install chromium --with-deps

# Copy application code
COPY . .

# supervisord config — runs web + celery worker + celery beat in one container
RUN mkdir -p /var/log/supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8000

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
