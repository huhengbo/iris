# syntax=docker/dockerfile:1

# Web 产物与 CPU 架构无关，固定在构建机平台上编译，多架构构建时只编一次
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS client
ARG FLUTTER_VERSION=3.47.2
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl git unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${FLUTTER_VERSION}" https://github.com/flutter/flutter.git /opt/flutter
ENV PATH="/opt/flutter/bin:${PATH}" \
    FLUTTER_SUPPRESS_ANALYTICS=true
RUN flutter config --no-analytics --enable-web && flutter precache --web
WORKDIR /src/client
COPY client/pubspec.yaml client/pubspec.lock ./
RUN flutter pub get
COPY client/ ./
RUN flutter build web --release --no-web-resources-cdn

FROM python:3.13-slim AS backend-builder
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app/backend
# 宿主依赖 SDK 的本地路径包（../packages/iris-plugin-sdk）
COPY packages/ /app/packages/
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --locked --no-dev --no-editable

# 插件各自是独立包和独立 venv。先让 SDK 位于 /app/packages，再按 plugins/* 逐个同步；
# 插件目录还没有时循环为空，构建照常进行（宿主此时没有可加载的插件）。
FROM python:3.13-slim AS plugin-builder
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY . /src
RUN mkdir -p /app/plugins /app/packages \
    && cp -a /src/packages/. /app/packages/ \
    && if [ -d /src/plugins ]; then cp -a /src/plugins/. /app/plugins/; fi \
    && for project in /app/plugins/*/; do \
        [ -f "${project}pyproject.toml" ] || continue; \
        uv sync --frozen --project "$project" --no-dev; \
    done

FROM python:3.13-slim AS runtime
ARG APP_UID=1000
ARG APP_GID=1000
# 插件依赖安装在运行时按 uv.lock 重建，镜像里必须带 uv；不加 git（仓库插件禁止 git 源）
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_DATA_DIR=/data \
    APP_STATIC_DIR=/app/static \
    APP_PLUGINS_DIR=/app/plugins \
    UV_PYTHON_DOWNLOADS=never
RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /app \
    && chown app:app /data
COPY --from=backend-builder /opt/venv /opt/venv
COPY --from=plugin-builder --chown=app:app /app/plugins /app/plugins
COPY --from=plugin-builder --chown=app:app /app/packages /app/packages
COPY --from=client /src/client/build/web /app/static
USER app
WORKDIR /app
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).read()"
CMD ["uvicorn", "app.main:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
