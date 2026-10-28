FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir uv==0.12.19
COPY --chmod=644 pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --locked --no-dev
COPY --chmod=755 scripts ./scripts
COPY --chmod=755 config ./config
COPY --chmod=644 Dockerfile Dockerfile.cloud .dockerignore ./
USER 10001:10001
ENV BUNDLE_PATH=/bundle
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s CMD ["/app/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/ready', timeout=2)"]
CMD ["/app/.venv/bin/python", "-m", "scripts.local_api", "--host", "0.0.0.0"]
