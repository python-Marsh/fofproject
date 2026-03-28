FROM python:3.12-slim

WORKDIR /app

# System deps for matplotlib rendering + chromium for selenium (connection.py)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx libglib2.0-0 fonts-dejavu-core \
    chromium chromium-driver && \
    rm -rf /var/lib/apt/lists/*

# Install poetry and project deps
COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main

# Copy source code
COPY src/ ./src/
COPY web/ ./web/
COPY monitor.py ./

ENV PYTHONPATH=/app/src:/app
ENV MPLBACKEND=Agg
