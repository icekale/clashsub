FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run test && npm run build

FROM aethersailor/subconverter-extended:v1.2.0 AS converter

FROM python:3.13-slim-trixie AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIR=/app/frontend/dist \
    DATA_DIR=/data \
    TZ=Asia/Shanghai
WORKDIR /app
RUN sed -i 's|http://deb.debian.org/debian|http://mirrors.tuna.tsinghua.edu.cn/debian|g; s|http://security.debian.org/debian-security|http://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates libcurl4t64 libyaml-cpp0.8 libpcre2-8-0 libstdc++6 libgcc-s1 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 clashsub
COPY pyproject.toml ./
COPY src/ ./src/
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN PIP_DEFAULT_TIMEOUT=300 PIP_RETRIES=10 pip install --no-cache-dir --index-url "$PIP_INDEX_URL" .
COPY --from=frontend-build /frontend/dist /app/frontend/dist
COPY LICENSE THIRD_PARTY_NOTICES.md /app/

# SubConverter-Extended 转换服务（同一容器内的回环进程，仅监听 127.0.0.1:25500）。
COPY --from=converter /usr/bin/subconverter /usr/bin/subconverter
COPY --from=converter /usr/lib/libmihomo.so /usr/lib/libmihomo.so
COPY --from=converter /base /base
COPY --from=converter /usr/local/bin/start-subconverter /usr/local/bin/start-subconverter
RUN chmod +x /usr/local/bin/start-subconverter \
    && mkdir -p /data \
    && chown -R clashsub:clashsub /data /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "clashsub.app:production_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
