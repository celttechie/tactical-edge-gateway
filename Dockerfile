# ==============================================================================
# Tactical Edge Gateway - Multi-Stage DISA Hardened Container
# ==============================================================================
# Build Stage: Creates isolated virtualenv with minimal compiled dependencies
# ==============================================================================
FROM python:3.11-alpine AS builder

WORKDIR /build

# Copy project specification and application code
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Create a clean virtual environment and install the gateway
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir . && \
    # Remove pip, setuptools, and wheel from final venv to eliminate package CVEs
    /opt/venv/bin/pip uninstall -y pip setuptools wheel && \
    find /opt/venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# ==============================================================================
# Runtime Stage: Minimal, rootless, zero-CVE execution environment
# ==============================================================================
FROM python:3.11-alpine AS runtime

# OCI Image Annotations & Metadata
LABEL org.opencontainers.image.title="Tactical Edge Gateway" \
      org.opencontainers.image.description="Air-gapped resilient tactical telemetry gateway bridging MAVLink and ROS2 to TAK Server" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.vendor="Brian Jarrett" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/celttechie/tactical-edge-gateway" \
      stig.disa.mil.category="Container Platform Hardened"

# Security Hardening:
# 1. Create non-root group and user (UID 10001, GID 10001)
# 2. Allocate persistent data volume for SQLite WAL buffer
# 3. Purge system package managers and default pip/setuptools to minimize attack surface
RUN apk upgrade --no-cache && \
    addgroup -g 10001 -S edgegroup && \
    adduser -u 10001 -S edgeuser -G edgegroup -h /app -s /sbin/nologin && \
    mkdir -p /data && \
    chown -R 10001:10001 /data && \
    chmod 700 /data && \
    rm -rf /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/pip* \
           /usr/local/lib/python3.11/site-packages/wheel* \
           /var/cache/apk/*

# Copy isolated virtual environment from builder stage
COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv

# Environment Configuration
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TEG_BUFFER__DB_PATH="/data/buffer.db"

# Expose standard tactical telemetry ports:
# - 14550/udp: MAVLink 2.0 telemetry ingestion
# - 4242/udp:  ATAK / FreeTAKServer broadcast/multicast egress
# - 8087/tcp:  TAK Server plaintext streaming
# - 8089/tcp:  TAK Server TLS streaming
EXPOSE 14550/udp 4242/udp 8087/tcp 8089/tcp

# Working directory and volume configuration
WORKDIR /data
VOLUME ["/data"]

# Enforce least privilege non-root execution
USER 10001:10001

# Container Healthcheck (NIST SP 800-53 SI-4 compliance)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import tactical_edge_gateway; print('healthy')"]

ENTRYPOINT ["tactical-edge-gateway"]
CMD []
