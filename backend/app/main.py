from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import playback, plugin_ui
from app.bootstrap import build_container
from app.clock import Clock
from app.config import AppConfig, load_config
from app.emby.middleware import install_emby_rewrite
from app.errors import install_error_handlers, install_openapi_errors
from app.http_middleware import install_api_timing
from app.ops.log_buffer import install as install_log_buffer
from app.plugins.fetch import SafeFetcher
from app.plugins.installer import DependencyInstaller
from app.routes import install_routes
from app.sync.engine import Executor
from app.sync.run import Sleeper
from app.workers import BackgroundWorkers

REQUEST_THREADS = 200


def create_app(
    config: AppConfig,
    clock: Clock | None = None,
    *,
    sync_sleeper: Sleeper | None = None,
    sync_executor: Executor | None = None,
    delete_executor: Executor | None = None,
    background_tasks: bool = True,
    plugin_installer: DependencyInstaller | None = None,
    plugin_fetcher: SafeFetcher | None = None,
) -> FastAPI:
    """background_tasks 控制是否启动定时同步等后台线程，测试里关闭后手动驱动。"""
    container = build_container(
        config,
        clock,
        sync_sleeper=sync_sleeper,
        sync_executor=sync_executor,
        delete_executor=delete_executor,
        plugin_installer=plugin_installer,
        plugin_fetcher=plugin_fetcher,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 同步接口跑在线程池里。写请求排队等写锁会占着线程，默认的 40 个线程被占满后，
        # 连只读请求也没线程可用；这里留出余量，读请求不受写队列影响
        to_thread.current_default_thread_limiter().total_tokens = REQUEST_THREADS
        workers = BackgroundWorkers(container)
        if background_tasks:
            workers.start()
        yield
        workers.stop()
        container.plugins.shutdown()

    app = FastAPI(
        title="光圈",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.container = container
    install_error_handlers(app)
    install_openapi_errors(app)
    install_api_timing(app)
    install_emby_rewrite(app)
    if config.cors_origins:
        # 只用 Bearer 令牌鉴权，不需要跨域携带 Cookie
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )
    plugin_ui.install_plugin_cors(app)
    install_routes(app, config)
    return app


def create_app_from_env() -> FastAPI:
    # uvicorn 只配置它自己的日志器；应用日志（同步进度、播放跳转）也要进 docker logs
    logging.basicConfig(format="%(levelname)s:     %(name)s: %(message)s")
    logging.getLogger("app").setLevel(logging.INFO)
    install_log_buffer()
    logging.getLogger("uvicorn.access").addFilter(playback.RedactPlayTokens())
    return create_app(load_config())
