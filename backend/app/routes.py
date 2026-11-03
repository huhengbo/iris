"""路由装配：接口路由挂在 /api 下，播放跳转、WebDAV 和插件页面挂在根路径。"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from app.api import (
    ai,
    ai_chat,
    auth,
    browse,
    collections,
    favorites,
    health,
    images,
    playback,
    playlists,
    plugin_ui,
    preferences,
    related,
    sessions,
    subtitles,
    system,
    watchlist,
    webdav,
)
from app.api.admin import (
    activity as admin_activity,
)
from app.api.admin import (
    ai as admin_ai,
)
from app.api.admin import (
    alerts as admin_alerts,
)
from app.api.admin import (
    artwork as admin_artwork,
)
from app.api.admin import (
    backups as admin_backups,
)
from app.api.admin import (
    health as admin_health,
)
from app.api.admin import (
    history as admin_history,
)
from app.api.admin import (
    item_edit as admin_item_edit,
)
from app.api.admin import (
    items as admin_items,
)
from app.api.admin import (
    libraries as admin_libraries,
)
from app.api.admin import (
    library_downloaders as admin_library_downloaders,
)
from app.api.admin import (
    library_settings as admin_library_settings,
)
from app.api.admin import (
    logs as admin_logs,
)
from app.api.admin import (
    maintenance as admin_maintenance,
)
from app.api.admin import (
    pending as admin_pending,
)
from app.api.admin import (
    plugin_repositories as admin_plugin_repositories,
)
from app.api.admin import (
    plugins as admin_plugins,
)
from app.api.admin import (
    resources as admin_resources,
)
from app.api.admin import (
    seasons as admin_seasons,
)
from app.api.admin import (
    settings as admin_settings,
)
from app.api.admin import (
    setup as admin_setup,
)
from app.api.admin import (
    summary as admin_summary,
)
from app.api.admin import (
    sync as admin_sync,
)
from app.api.admin import (
    tasks as admin_tasks,
)
from app.api.admin import (
    users as admin_users,
)
from app.api.admin import (
    webhooks as admin_webhooks,
)
from app.config import AppConfig
from app.emby import routes as emby
from app.web import install_web_client


def install_routes(app: FastAPI, config: AppConfig) -> None:
    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(system.router)
    api.include_router(images.router)
    api.include_router(auth.router)
    api.include_router(browse.router)
    api.include_router(related.router)
    api.include_router(favorites.router)
    api.include_router(watchlist.router)
    api.include_router(playback.router)
    api.include_router(subtitles.router)
    api.include_router(preferences.router)
    api.include_router(webdav.router)
    api.include_router(admin_users.router)
    api.include_router(admin_backups.router)
    api.include_router(admin_health.router)
    api.include_router(admin_libraries.router)
    api.include_router(admin_library_downloaders.router)
    api.include_router(admin_sync.router)
    api.include_router(admin_settings.router)
    api.include_router(admin_summary.router)
    api.include_router(admin_pending.router)
    api.include_router(admin_resources.router)
    api.include_router(admin_plugins.router)
    api.include_router(admin_plugin_repositories.router)
    api.include_router(admin_plugin_repositories.catalog_router)
    api.include_router(plugin_ui.admin_router)
    api.include_router(plugin_ui.api_router)
    api.include_router(admin_setup.router)
    api.include_router(admin_tasks.router)
    api.include_router(admin_seasons.router)
    api.include_router(admin_artwork.router)
    api.include_router(admin_item_edit.router)
    api.include_router(collections.router)
    api.include_router(admin_alerts.router)
    api.include_router(admin_history.router)
    api.include_router(admin_maintenance.router)
    api.include_router(admin_library_settings.router)
    api.include_router(admin_webhooks.router)
    api.include_router(sessions.router)
    api.include_router(admin_logs.router)
    api.include_router(collections.public_router)
    api.include_router(playlists.public_router)
    api.include_router(admin_library_settings.public_router)
    api.include_router(sessions.public_router)
    api.include_router(admin_items.router)
    api.include_router(admin_activity.router)
    api.include_router(admin_ai.router)
    api.include_router(ai.router)
    api.include_router(ai_chat.router)
    app.include_router(api)
    app.include_router(playback.redirect_router)
    app.include_router(emby.router)
    app.include_router(webdav.webdav_router)
    app.include_router(plugin_ui.page_router)
    # 前端兜底路由必须最后注册
    install_web_client(app, config.static_dir)
