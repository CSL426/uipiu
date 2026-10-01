FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runner

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
RUN useradd --create-home --uid 1000 appuser
COPY --chown=appuser:appuser app ./app
RUN mkdir -p /app/app/data && chown appuser:appuser /app/app/data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM runner AS tester
USER root
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY tests ./tests
COPY scripts ./scripts
COPY pytest.ini compose.yaml Dockerfile .env.example ./
COPY nginx/default.conf.template ./nginx/default.conf.template
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/Dockerfile ./frontend/
COPY frontend/docker/entrypoint.sh ./frontend/docker/entrypoint.sh
COPY frontend/src ./frontend/src
USER appuser
ENV CART_DB_PATH=/tmp/nidin-tests/cart.db
CMD ["python", "-m", "pytest", "-q", "--basetemp=/home/appuser/pytest-tmp", "-o", "cache_dir=/tmp/nidin-tests/pytest-cache"]
