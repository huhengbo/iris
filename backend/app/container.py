"""应用级依赖容器。

所有可替换的基础设施和业务服务在启动时组装一次；路由通过 deps 读取容器，
测试只需替换 create_app 的构造参数即可使用假网关、时钟和执行器。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.actions import PendingActions
from app.ai.ask import AskSearch
from app.ai.chat import ChatService
from app.ai.semantic import SemanticIndex
from app.ai.service import AiService
from app.ai.settings import AiSettings
from app.ai.suggestions import SuggestionService
from app.ai.tools import ToolRegistry
from app.auth.throttle import LoginThrottle
from app.auth.tokens import TokenSigner
from app.auth.webdav import WebDavService
from app.clock import Clock
from app.config import AppConfig
from app.db import Database
from app.library.curation import Curation
from app.library.images import ImageStore
from app.library.metadata import Metadata
from app.ops.backup import BackupService
from app.playback.service import PlaybackService
from app.plugins.packages import PluginPackages
from app.plugins.registry import PluginRegistry
from app.plugins.repositories import PluginRepositories
from app.plugins.supervisor import PluginSupervisor
from app.resources.service import ResourceSources
from app.settings import SettingsService
from app.subtitles.auto import SubtitleAutoDownloader
from app.subtitles.online import OnlineSubtitles
from app.sync.engine import Executor, SyncEngine
from app.sync.reenrich import Reenricher
from app.sync.schedule import SyncSchedule


@dataclass(frozen=True)
class AppContainer:
    config: AppConfig
    clock: Clock
    database: Database
    backups: BackupService
    settings: SettingsService
    access_signer: TokenSigner
    login_throttle: LoginThrottle
    ai: AiService
    semantic: SemanticIndex
    tools: ToolRegistry
    ai_settings: AiSettings
    actions: PendingActions
    suggestions: SuggestionService
    ask: AskSearch
    chat: ChatService
    playback: PlaybackService
    subtitles: OnlineSubtitles
    subtitle_auto: SubtitleAutoDownloader
    plugins: PluginRegistry
    supervisor: PluginSupervisor
    plugin_packages: PluginPackages
    plugin_repositories: PluginRepositories
    resources: ResourceSources
    images: ImageStore
    metadata: Metadata
    curation: Curation
    reenricher: Reenricher
    sync: SyncEngine
    sync_schedule: SyncSchedule
    delete_executor: Executor
    webdav: WebDavService
