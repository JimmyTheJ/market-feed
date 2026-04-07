FROM python:3.12-slim

LABEL maintainer="market-pipeline"
LABEL description="Position-driven market intelligence pipeline"

# Avoid bytecode and buffer output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config/ config/
COPY data/ data/
COPY src/ src/
COPY web/ web/

# Create output and logs directories
RUN mkdir -p output logs data/cache

# Expose API port
EXPOSE 8000

# Default: run the API server with built-in scheduler
CMD ["python", "-m", "uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
