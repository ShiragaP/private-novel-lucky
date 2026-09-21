FROM python:3.12-slim

WORKDIR /app

# Install build dependencies for psycopg2 and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and assets
COPY . .

# Expose app port
EXPOSE 21041

ENV PORT=21041
ENV PYTHONUNBUFFERED=1

CMD ["python", "server.py"]
