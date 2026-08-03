FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run test && npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIR=/app/frontend/dist \
    DATA_DIR=/data \
    TZ=Asia/Shanghai
WORKDIR /app
RUN sed -i 's|http://deb.debian.org/debian|http://mirrors.tuna.tsinghua.edu.cn/debian|g; s|http://security.debian.org/debian-security|http://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 clashsub
COPY pyproject.toml ./
COPY src/ ./src/
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN PIP_DEFAULT_TIMEOUT=300 PIP_RETRIES=10 pip install --no-cache-dir --index-url "$PIP_INDEX_URL" .
COPY --from=frontend-build /frontend/dist /app/frontend/dist
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
RUN mkdir -p /data && chown -R clashsub:clashsub /data /app
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "clashsub.app:production_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
