# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


# Stage 2: Final lightweight image
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy codebase
COPY . .

# Set environment paths and configurations
ENV PYTHONPATH=/app
ENV PORT=8080

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose standard ports
EXPOSE 8000
EXPOSE 8501
EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
