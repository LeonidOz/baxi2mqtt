# ================================
# Builder Stage: Install dependencies
# ================================
FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for clean isolation
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies into virtual environment
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ================================
# Production Stage: Runtime image
# ================================
FROM python:3.12-slim AS production

# Runtime image does not require additional OS packages.

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create application directory
WORKDIR /app
RUN chown -R appuser:appuser /app

# Create directories for logs and config
RUN mkdir -p /app/logs /app/config && \
    chown -R appuser:appuser /app/logs /app/config

# Copy application files
COPY --chown=appuser:appuser app.py config_validator.py reconnection_manager.py health_checker.py homeassistant_discovery.py container_healthcheck.py ./
COPY --chown=appuser:appuser config/ ./config/

# Switch to non-root user
USER appuser

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "container_healthcheck.py"]

# Expose health check port
EXPOSE 8080

# Default command
CMD ["python", "app.py"]

# ================================
# Labels for better container metadata
# ================================
LABEL org.opencontainers.image.title="BaxiMQTT" \
      org.opencontainers.image.description="Baxi Connect+ to MQTT bridge with Home Assistant support" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.authors="BaxiMQTT Team" \
      org.opencontainers.image.source="https://github.com/your-org/baxi2mqtt" \
      org.opencontainers.image.licenses="MIT"

# ================================
# Security annotations
# ================================
LABEL org.opencontainers.image.security.scan="true" \
      org.opencontainers.image.security.cve-scan="true"
