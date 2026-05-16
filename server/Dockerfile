# ===== BUILDER STAGE =====
FROM python:3.11-slim as builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install without uvloop dependency on cmd
RUN pip install --user --no-cache-dir --compile -r requirements.txt

# ===== RUNTIME STAGE =====
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    # Tell the app MongoDB is at the compose service name, not localhost
    MONGO_URI=mongodb://mongo:27017/rag_database \
    OPENMETADATA_URL=http://openmetadata:8585

COPY --chown=appuser:appuser . .

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# Removed --loop uvloop (not guaranteed to be installed)
CMD ["uvicorn", "app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--no-access-log"]