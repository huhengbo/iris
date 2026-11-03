from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from app.ai.actions import PendingActions
from app.ai.ask import AskSearch
from app.ai.chat import ChatService
from app.ai.semantic import SemanticIndex
from app.ai.service import AiService
from app.ai.settings import AiSettings
from app.ai.suggestions import SuggestionService
from app.ai.tools import ToolRegistry
from app.auth.sessions import SessionService, Unauthorized
from app.auth.throttle import LoginThrottle
from app.auth.tokens import TokenSigner
from app.auth.users import User
from app.auth.webdav import WebDavService
from app.clock import Clock
from app.container import AppContainer
from app.db import Database
from app.errors import AppError
from app.library.curation import Curation
from app.library.images import ImageStore
from app.library.metadata import Metadata
from app.library.metadata_providers import MetadataProviders
from app.ops.backup import BackupService
from app.playback.service import PlaybackService
from app.plugins.packages import PluginPackages
from app.plugins.registry import PluginRegistry
from app.plugins.repositories import PluginRepositories
from app.resources.service import ResourceSources
from app.settings import SettingsService
from app.subtitles.auto import SubtitleAutoDownloader
from app.subtitles.online import OnlineSubtitles
from app.sync.engine import Executor, SyncEngine
from app.sync.reenrich import Reenricher
from app.sync.schedule import SyncSchedule


def container_of(request: Request) -> AppContainer:
    """容器是路由取得所有服务的唯一入口；不走 Depends 的辅助函数（如 plugin_ui）直接用它。"""
    container: AppContainer = request.app.state.container
    return container


def get_container(request: Request) -> AppContainer:
    return container_of(request)


Container = Annotated[AppContainer, Depends(get_container)]


def get_db(container: Container) -> Iterator[sqlite3.Connection]:
    with container.database.session() as connection:
        yield connection


def get_read_db(container: Container) -> Iterator[sqlite3.Connection]:
    with container.database.read_session() as connection:
        yield connection


def get_database(container: Container) -> Database:
    """需要在请求中途看到其他事务写入结果的接口用它自己开会话（Db 的快照在请求开始时就固定了）。"""
    return container.database


def get_clock(container: Container) -> Clock:
    return container.clock


def get_settings(container: Container) -> SettingsService:
    return container.settings


def get_ai(container: Container) -> AiService:
    return container.ai


def get_tools(container: Container) -> ToolRegistry:
    return container.tools


def get_ai_settings(container: Container) -> AiSettings:
    return container.ai_settings


def get_suggestions(container: Container) -> SuggestionService:
    return container.suggestions


def get_semantic(container: Container) -> SemanticIndex:
    return container.semantic


def get_ask(container: Container) -> AskSearch:
    return container.ask


def get_chat(container: Container) -> ChatService:
    return container.chat


def get_actions(container: Container) -> PendingActions:
    return container.actions


def get_curation(container: Container) -> Curation:
    return container.curation


def get_playback(container: Container) -> PlaybackService:
    return container.playback


def get_online_subtitles(container: Container) -> OnlineSubtitles:
    return container.subtitles


def get_subtitle_auto(container: Container) -> SubtitleAutoDownloader:
    return container.subtitle_auto


def get_metadata(container: Container) -> Metadata:
    return container.metadata


def get_metadata_providers(container: Container) -> MetadataProviders:
    return container.metadata.providers


def get_images(container: Container) -> ImageStore:
    return container.images


def get_plugins(container: Container) -> PluginRegistry:
    return container.plugins


def get_plugin_packages(container: Container) -> PluginPackages:
    return container.plugin_packages


def get_plugin_repositories(container: Container) -> PluginRepositories:
    return container.plugin_repositories


def get_resources(container: Container) -> ResourceSources:
    return container.resources


def get_backups(container: Container) -> BackupService:
    return container.backups


def get_delete_executor(container: Container) -> Executor:
    return container.delete_executor


def get_webdav(container: Container) -> WebDavService:
    return container.webdav


def get_sync(container: Container) -> SyncEngine:
    return container.sync


def get_schedule(container: Container) -> SyncSchedule:
    return container.sync_schedule


def get_reenricher(container: Container) -> Reenricher:
    return container.reenricher


def get_login_throttle(container: Container) -> LoginThrottle:
    return container.login_throttle


def get_access_signer(container: Container) -> TokenSigner:
    return container.access_signer


# 必须在响应发出前提交：默认的 request 作用域会在响应之后才 COMMIT，
# 客户端紧接着发的请求会读到旧快照，写入时报 database is locked
Db = Annotated[sqlite3.Connection, Depends(get_db, scope="function")]
ReadDb = Annotated[sqlite3.Connection, Depends(get_read_db, scope="function")]
AppClock = Annotated[Clock, Depends(get_clock)]
AppDatabase = Annotated[Database, Depends(get_database)]
Settings = Annotated[SettingsService, Depends(get_settings)]
Ai = Annotated[AiService, Depends(get_ai)]
Tools = Annotated[ToolRegistry, Depends(get_tools)]
AiConfig = Annotated[AiSettings, Depends(get_ai_settings)]
Suggestions = Annotated[SuggestionService, Depends(get_suggestions)]
Semantic = Annotated[SemanticIndex, Depends(get_semantic)]
Ask = Annotated[AskSearch, Depends(get_ask)]
Chats = Annotated[ChatService, Depends(get_chat)]
Actions = Annotated[PendingActions, Depends(get_actions)]
AppCuration = Annotated[Curation, Depends(get_curation)]
Playback = Annotated[PlaybackService, Depends(get_playback)]
OnlineSubtitlesDep = Annotated[OnlineSubtitles, Depends(get_online_subtitles)]
SubtitleAuto = Annotated[SubtitleAutoDownloader, Depends(get_subtitle_auto)]
AppMetadata = Annotated[Metadata, Depends(get_metadata)]
MetadataPlugins = Annotated[MetadataProviders, Depends(get_metadata_providers)]
Images = Annotated[ImageStore, Depends(get_images)]
Plugins = Annotated[PluginRegistry, Depends(get_plugins)]
PluginPackagesDep = Annotated[PluginPackages, Depends(get_plugin_packages)]
PluginRepositoriesDep = Annotated[PluginRepositories, Depends(get_plugin_repositories)]
Resources = Annotated[ResourceSources, Depends(get_resources)]
Backups = Annotated[BackupService, Depends(get_backups)]
DeleteExecutor = Annotated[Executor, Depends(get_delete_executor)]
WebDav = Annotated[WebDavService, Depends(get_webdav)]
Sync = Annotated[SyncEngine, Depends(get_sync)]
Schedule = Annotated[SyncSchedule, Depends(get_schedule)]
Reenrich = Annotated[Reenricher, Depends(get_reenricher)]
LoginThrottleDep = Annotated[LoginThrottle, Depends(get_login_throttle)]
AccessSigner = Annotated[TokenSigner, Depends(get_access_signer)]


def get_sessions(db: Db, clock: AppClock, signer: AccessSigner) -> SessionService:
    return SessionService(db, clock, signer)


Sessions = Annotated[SessionService, Depends(get_sessions)]


def get_current_user(request: Request, clock: AppClock) -> User:
    """认证只读一次用户表：用短暂的只读连接，不占写锁，也不把快照带进后面的处理函数。"""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("请先登录")
    container = container_of(request)
    with container.database.read_session() as connection:
        return SessionService(connection, clock, container.access_signer).authenticate(token)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise AppError(403, "forbidden", "需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def require_subtitle_download(user: CurrentUser) -> User:
    """在线字幕的搜索和下载：管理员始终允许，普通用户看账号权限。"""
    if not user.is_admin and not user.allow_subtitle_download:
        raise AppError(403, "subtitle_download_not_allowed", "管理员不允许你下载在线字幕")
    return user


SubtitleDownloadUser = Annotated[User, Depends(require_subtitle_download)]
