"""组装应用容器：创建数据库、发现插件，并把各服务装配成 AppContainer。

create_app 的构造参数用于测试替换时钟和执行器；生产环境全部取默认值。插件只从宿主配置的
插件目录发现，进程内运行插件代码的路径已经删除。
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from app.auth.throttle import LoginThrottle
from app.auth.tokens import TokenSigner
from app.auth.users import UserRepository
from app.auth.webdav import WebDavService
from app.clock import Clock, SystemClock
from app.config import AppConfig
from app.container import AppContainer
from app.db import Database
from app.library.curation import Curation
from app.library.images import ImageStore
from app.library.metadata import Metadata
from app.library.metadata_providers import MetadataProviders
from app.ops.backup import BackupService
from app.ops.tasks import TaskRepository
from app.playback.service import PlaybackService
from app.plugins.fetch import SafeFetcher
from app.plugins.host import effective_proxy
from app.plugins.installer import DependencyInstaller, UvInstaller
from app.plugins.packages import PluginPackages
from app.plugins.registry import PluginRegistry
from app.plugins.repositories import PluginRepositories
from app.plugins.supervisor import PluginSupervisor
from app.resources.service import ResourceSources
from app.settings import SettingsService
from app.subtitles.auto import SubtitleAutoDownloader
from app.subtitles.online import OnlineSubtitles
from app.sync.engine import Executor, SyncEngine, thread_executor
from app.sync.jobs import SyncJobRepository
from app.sync.reenrich import Reenricher
from app.sync.run import Sleeper
from app.sync.schedule import SyncSchedule


def build_container(
    config: AppConfig,
    clock: Clock | None = None,
    *,
    sync_sleeper: Sleeper | None = None,
    sync_executor: Executor | None = None,
    delete_executor: Executor | None = None,
    plugin_installer: DependencyInstaller | None = None,
    plugin_fetcher: SafeFetcher | None = None,
) -> AppContainer:
    clock = clock or SystemClock()
    database = Database(config.database_path)
    database.migrate(clock)
    _bootstrap_admin(database, clock, config)
    settings = SettingsService(database, clock)
    settings.ensure_server_id()
    supervisor = PluginSupervisor(
        config.plugins_dir,
        config.data_dir,
        host_url=settings.public_url,
        media_dir=config.media_dir,
        proxy=lambda plugin_id: effective_proxy(database, plugin_id),
    )
    plugins = PluginRegistry(database, clock, supervisor)
    repositories = PluginRepositories(
        database,
        clock,
        fetcher=plugin_fetcher,
        proxy=settings.plugin_proxy,
        system_plugin_ids=plugins.built_in_ids,
    )
    repositories.ensure_defaults(config.default_plugin_repos)
    packages = PluginPackages(
        database,
        clock,
        config,
        supervisor,
        plugins,
        repositories,
        plugin_installer or UvInstaller(config.plugin_packages_dir / ".uv-cache"),
        fetcher=plugin_fetcher,
    )
    plugins.attach_repositories(repositories)
    plugins.attach_packages(packages)
    packages.load_installed()
    webdav_service = WebDavService(database, clock)
    with database.session() as connection:
        SyncJobRepository(connection, clock).fail_interrupted()
        TaskRepository(connection, clock).fail_interrupted()

    backups = BackupService(database, config.data_dir, clock, plugin_maintenance=packages)
    access_signer = TokenSigner(config.secret_key, "access-token")
    login_throttle = LoginThrottle(clock)
    plugins.start_enabled()
    resources = ResourceSources(plugins)
    online_subtitles = OnlineSubtitles(
        database, clock, plugins, resources, on_change=webdav_service.invalidate
    )
    subtitle_auto = SubtitleAutoDownloader(database, clock, plugins, online_subtitles)
    playback_service = PlaybackService(
        database,
        clock,
        TokenSigner(config.secret_key, "play-token"),
        settings,
        resources,
        media_dir=config.media_dir,
    )
    image_store = ImageStore(config.data_dir / "images", clock)
    metadata = Metadata.create(database, clock, MetadataProviders(database, plugins), image_store)
    curation = Curation(database, clock, metadata, resources)
    reenricher = Reenricher(database, clock, resources)
    sync = SyncEngine(
        database,
        clock,
        metadata,
        settings,
        resources,
        sleeper=sync_sleeper,
        executor=sync_executor,
    )
    plugins.set_resource_changed_handler(sync.source_changed)
    supervisor.set_resource_changed_handler(plugins.resource_changed)
    sync_schedule = SyncSchedule(database, clock, ZoneInfo(config.timezone), sync)
    container = AppContainer(
        config=config,
        clock=clock,
        database=database,
        backups=backups,
        settings=settings,
        access_signer=access_signer,
        login_throttle=login_throttle,
        playback=playback_service,
        subtitles=online_subtitles,
        subtitle_auto=subtitle_auto,
        plugins=plugins,
        supervisor=supervisor,
        plugin_packages=packages,
        plugin_repositories=repositories,
        resources=resources,
        images=image_store,
        metadata=metadata,
        curation=curation,
        reenricher=reenricher,
        sync=sync,
        sync_schedule=sync_schedule,
        delete_executor=delete_executor or thread_executor,
        webdav=webdav_service,
    )
    return container


def _bootstrap_admin(database: Database, clock: Clock, config: AppConfig) -> None:
    """数据库里还没有管理员时，用环境变量创建首个管理员；之后环境变量不再生效。"""
    with database.session() as connection:
        users = UserRepository(connection, clock)
        if not users.has_admin():
            users.create(config.admin_username, config.admin_password, "admin")
