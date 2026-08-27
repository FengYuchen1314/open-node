# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm@sha256:0bee7276f83efd4a1ee05bbbf4281d95ed28e079220a9457f25a93e3f1e3c31b AS python-base

FROM python-base AS backend
WORKDIR /build
COPY backend/ ./backend/
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir ./backend

FROM python-base AS lego
ARG TARGETARCH
COPY scripts/container/fetch-lego.py /fetch-lego.py
RUN python /fetch-lego.py "$TARGETARCH" /out

FROM python-base AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Open Node" \
      org.opencontainers.image.source="https://github.com/FengYuchen1314/open-node" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="$VCS_REF"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPEN_NODE_DATABASE_URL="sqlite:////var/lib/open-node/open-node.db" \
    OPEN_NODE_CERTIFICATE_STATE_DIR="/var/lib/open-node/certificates" \
    OPEN_NODE_CERTIFICATE_LEGO_BINARY="/usr/local/bin/lego" \
    OPEN_NODE_FRONTEND_DIR="/opt/open-node/frontend" \
    OPEN_NODE_CORS_ORIGINS="[]" \
    FORWARDED_ALLOW_IPS=""
RUN groupadd --gid 10001 open-node && useradd --uid 10001 --gid 10001 --no-create-home open-node \
    && install -d -m 0700 -o 10001 -g 10001 /var/lib/open-node
COPY --from=backend /opt/venv /opt/venv
COPY --from=frontend /build/dist /opt/open-node/frontend
COPY --from=lego /out/lego /usr/local/bin/lego
COPY --from=lego /out/LICENSE /usr/share/licenses/lego/LICENSE
COPY LICENSE /usr/share/licenses/open-node/LICENSE
COPY --chmod=755 scripts/container/entrypoint.sh /usr/local/bin/open-node-entrypoint
WORKDIR /opt/open-node
USER 10001:10001
VOLUME ["/var/lib/open-node"]
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]
ENTRYPOINT ["open-node-entrypoint"]
CMD ["uvicorn", "open_node.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
