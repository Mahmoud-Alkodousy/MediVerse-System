# ══════════════════════════════════════════════════════════════
#  MediVerse - Dockerfile
#  Smart Hospital System with AI Integration
# ══════════════════════════════════════════════════════════════

FROM python:3.11-slim

# ── System dependencies (ODBC driver for SQL Server + build tools) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    unixodbc \
    unixodbc-dev \
    apt-transport-https \
    build-essential \
    g++ \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──
WORKDIR /app

# ── Install Python dependencies ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy project ──
COPY . .

# ── Create necessary directories ──
RUN mkdir -p temp_uploads medical_files

# ── Expose port ──
EXPOSE 8000

# ── Health check ──
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── Run ──
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
