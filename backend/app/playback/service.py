"""播放：签发播放令牌和跳转到 115 直链（ADR 0003、0015）。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from iris_plugin_sdk import (
    PlaybackTarget,
    ResourceNotFound,
    ResourceRateLimited,
    ResourceUnavailable,
)

from app.auth.tokens import TokenSigner
from app.auth.users import User, UserRepository
from app.clock import Clock
from app.db import Database
from app.errors import AppError, NotFound
from app.library.browse import visible_library_ids
from app.playback.state import PlaybackStates, PlayClient
from app.playback.throttle import PlayThrottle
from app.resources.service import FolderSource, ResourceSources
from app.settings import SettingsService
from app.subtitles.content import decompress, normalize_language_code, subtitle_media_type

# 固定 24 小时，不提供配置项（ADR 0015）
PLAY_TOKEN_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class InlineContent:
    """播放时直接由本服务返回的内容（在线字幕），不经过资源来源。"""

    content: bytes
    media_type: str
    filename: str


@dataclass(frozen=True)
class PlaySubtitle:
    id: int
    label: str
    language: str | None
    forced: bool
    # 未配置对外访问地址时只有 path（只有内置播放器能这样签发）
    url: str | None
    path: str
    source: str = "external"


@dataclass(frozen=True)
class LaunchLinks:
    """各本机播放器的调起链接。iOS 的 Scheme 和字幕参数以 ADR 0012 记录的实测结果为准。"""

    infuse: str
    vlc: str
    nplayer: str
    senplayer: str
    android: str


@dataclass(frozen=True)
class PlayTicket:
    playable_id: int
    version_id: int
    # 播放器里显示的名字，例如「三体 S01E02」
    title: str
    filename: str
    # 对外访问地址加上 path；未配置对外访问地址时为 None，App 用自己连的服务器地址拼 path
    url: str | None
    path: str
    expires_at: datetime
    # 第一条是默认字幕，调起链接里带的就是它
    subtitles: list[PlaySubtitle]
    selected_subtitle_id: int | None
    launch: LaunchLinks | None
    # 上次停下的位置（秒），App 的内置播放器从这里续播；没有记录时为 None
    position: int | None


class PlayDenied(Exception):
    """跳转失败。播放器只认状态码，响应体是给人看的简短说明。"""

    def __init__(self, status: int, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class _Target:
    version_id: int
    playable_id: int
    title: str
    rel_path: str
    playback_locator: str
    folder_id: int


class PlaybackService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        signer: TokenSigner,
        settings: SettingsService,
        resources: ResourceSources,
        *,
        media_dir: Path,
    ) -> None:
        self._database = database
        self._clock = clock
        self._signer = signer
        self._settings = settings
        self._resources = resources
        self._media_dir = media_dir.resolve()
        self._throttle = PlayThrottle(clock)

    # ---- 签发 ----

    def issue(
        self,
        connection: sqlite3.Connection,
        user: User,
        *,
        version_id: int | None = None,
        item_id: int | None = None,
        episode_id: int | None = None,
        subtitle_id: int | None = None,
        client: PlayClient = "web",
        builtin: bool = False,
    ) -> PlayTicket:
        """三选一：指定版本，或者电影、单集（用默认版本）。范围外的一律当作不存在。

        外部播放器要用对外访问地址；App 的内置播放器（builtin）连的就是这台服务器，
        没配置对外访问地址也能播。
        """
        public_url = self._settings.public_url()
        if public_url is None and not builtin:
            raise AppError(
                409, "public_url_not_configured", "管理员还没有配置对外访问地址，暂时不能播放"
            )
        if version_id is None:
            playable_id = self._playable(connection, user, item_id, episode_id)
            version_id = self.default_version(connection, user, playable_id)
        target = _target(connection, user, version_id)
        if target is None:
            raise NotFound("这个版本不存在或已失效")
        expires_at = self._clock.now() + PLAY_TOKEN_TTL
        claims = {
            "typ": "play",
            "sub": user.id,
            "tv": user.token_version,
            "v": target.version_id,
            "exp": int(expires_at.timestamp()),
        }
        filename = _basename(target.rel_path)
        path = _play_path(self._signer.sign(claims), filename)
        subtitle_rows = _subtitles(connection, target.version_id, user.id)
        if subtitle_id is not None:
            selected = next((row for row in subtitle_rows if row["id"] == subtitle_id), None)
            if selected is None:
                raise NotFound("字幕不存在或已失效")
            subtitle_rows = [selected, *(row for row in subtitle_rows if row is not selected)]
        subtitles = []
        for row in subtitle_rows:
            subtitle_filename = _subtitle_filename(row, target.rel_path)
            subtitle_path = _play_path(
                self._signer.sign({**claims, "s": row["id"]}), subtitle_filename
            )
            subtitles.append(
                PlaySubtitle(
                    id=row["id"],
                    label=row["label"],
                    language=row["language"],
                    forced=bool(row["forced"]),
                    url=public_url + subtitle_path if public_url else None,
                    path=subtitle_path,
                    source=row["source"],
                )
            )
        states = PlaybackStates(connection, self._clock, user)
        position = states.position(target.playable_id)
        states.record_play(
            target.playable_id,
            target.version_id,
            client,
            mode="builtin" if builtin else "external",
        )
        url = public_url + path if public_url else None
        launch = None
        if url is not None:
            launch = launch_links(url, target.title, subtitles[0].url if subtitles else None)
        return PlayTicket(
            playable_id=target.playable_id,
            version_id=target.version_id,
            title=target.title,
            filename=filename,
            url=url,
            path=path,
            expires_at=expires_at,
            subtitles=subtitles,
            selected_subtitle_id=subtitles[0].id if subtitles else None,
            launch=launch,
            position=position,
        )

    def _playable(
        self,
        connection: sqlite3.Connection,
        user: User,
        item_id: int | None,
        episode_id: int | None,
    ) -> int:
        library_ids = visible_library_ids(connection, user)
        scope = ", ".join("?" for _ in library_ids) or "NULL"
        column = "p.episode_id" if episode_id is not None else "p.media_item_id"
        # 电影条目的可播放条目排在前面；剧集条目只有单集，下面按类型拒绝
        row = connection.execute(
            f"SELECT p.id, m.kind FROM playable p "
            f"JOIN media_item m ON m.id = p.media_item_id "
            f"WHERE {column} = ? AND m.library_id IN ({scope}) "
            "ORDER BY p.episode_id IS NOT NULL LIMIT 1",
            (episode_id if episode_id is not None else item_id, *library_ids),
        ).fetchone()
        if row is None:
            raise NotFound("条目不存在")
        if episode_id is None and row["kind"] == "series":
            raise AppError(400, "episode_required", "剧集要选一集来播放")
        return int(row["id"])

    def default_version(self, connection: sqlite3.Connection, user: User, playable_id: int) -> int:
        """上次用过的版本还可用就接着用，否则用最大的那个。"""
        row = connection.execute(
            """
            SELECT v.id FROM version v JOIN media_file c ON c.id = v.media_file_id
            LEFT JOIN playback_state s ON s.user_id = ? AND s.playable_id = v.playable_id
            WHERE v.playable_id = ? AND c.state = 'active' AND c.playback_locator IS NOT NULL
            ORDER BY COALESCE(v.id = s.last_version_id, 0) DESC, c.size IS NULL, c.size DESC,
                v.id
            LIMIT 1
            """,
            (user.id, playable_id),
        ).fetchone()
        if row is None:
            raise NotFound("没有可以播放的文件")
        return int(row["id"])

    # ---- 跳转 ----

    def resolve(self, token: str, user_agent: str) -> PlaybackTarget | InlineContent:
        """校验播放令牌并解析播放目标。每次都重新校验用户和可见范围（ADR 0015）。"""
        claims = self._signer.verify(token)
        if claims is None or claims.get("typ") != "play" or not _valid_claims(claims):
            raise PlayDenied(401, "播放地址无效")
        user_id, token_version, version_id, exp = (claims[k] for k in ("sub", "tv", "v", "exp"))
        subtitle_id: int | None = claims.get("s")
        if self._clock.now().timestamp() >= exp:
            raise PlayDenied(410, "播放地址已过期，请回到应用重新点播放")
        with self._database.read_session() as connection:
            user = UserRepository(connection, self._clock).get(user_id)
            if user is None or user.disabled or user.token_version != token_version:
                raise PlayDenied(401, "播放地址已失效，请回到应用重新点播放")
            return self._redirect(connection, user, version_id, user_agent, token, subtitle_id)

    def redirect_version(
        self,
        user: User,
        version_id: int,
        user_agent: str,
        throttle_key: str,
        *,
        subtitle_id: int | None = None,
    ) -> PlaybackTarget | InlineContent:
        """设备令牌已经校验过用户时用它，和播放令牌共用令牌校验之后的解析和限流逻辑。"""
        with self._database.read_session() as connection:
            return self._redirect(
                connection, user, version_id, user_agent, throttle_key, subtitle_id
            )

    def subtitle_link(
        self, user: User, version_id: int, subtitle_id: int
    ) -> tuple[str, str | None]:
        """在线字幕下载后返回的播放地址（24 小时签名），与 /play 签发规则一致。"""
        public_url = self._settings.public_url()
        expires_at = self._clock.now() + PLAY_TOKEN_TTL
        claims = {
            "typ": "play",
            "sub": user.id,
            "tv": user.token_version,
            "v": version_id,
            "s": subtitle_id,
            "exp": int(expires_at.timestamp()),
        }
        with self._database.read_session() as connection:
            target = _target(connection, user, version_id)
            if target is None:
                raise NotFound("版本不存在或已失效")
            subtitle = _subtitle_row(connection, version_id, subtitle_id)
            if subtitle is None:
                raise NotFound("字幕不存在或已失效")
            filename = _subtitle_filename(subtitle, target.rel_path)
        path = _play_path(self._signer.sign(claims), filename)
        return path, (public_url + path if public_url else None)

    def _redirect(
        self,
        connection: sqlite3.Connection,
        user: User,
        version_id: int,
        user_agent: str,
        throttle_key: str,
        subtitle_id: int | None = None,
    ) -> PlaybackTarget | InlineContent:
        target = _target(connection, user, version_id)
        if target is None:
            raise PlayDenied(404, "文件不存在或已失效")
        locator, folder_id = target.playback_locator, target.folder_id
        if subtitle_id is not None:
            subtitle = _subtitle_row(connection, version_id, subtitle_id)
            if subtitle is None:
                raise PlayDenied(404, "字幕不存在或已失效")
            if subtitle["source"] == "online":
                return InlineContent(
                    content=_online_content(connection, subtitle_id),
                    media_type=subtitle_media_type(str(subtitle["format"])),
                    filename=_subtitle_filename(subtitle, target.rel_path),
                )
            locator, folder_id = subtitle["playback_locator"], subtitle["folder_id"]
        source = ResourceSources.folder(connection, folder_id)
        return self._resolve(source, locator, user_agent, throttle_key)

    def inline_subtitle(self, user: User, version_id: int, subtitle_id: int) -> InlineContent:
        """WebDAV 等直接取在线字幕内容；外挂字幕仍走来源。"""
        with self._database.read_session() as connection:
            target = _target(connection, user, version_id)
            if target is None:
                raise PlayDenied(404, "文件不存在或已失效")
            subtitle = _subtitle_row(connection, version_id, subtitle_id)
            if subtitle is None or subtitle["source"] != "online":
                raise PlayDenied(404, "字幕不存在或已失效")
            return InlineContent(
                content=_online_content(connection, subtitle_id),
                media_type=subtitle_media_type(str(subtitle["format"])),
                filename=_subtitle_filename(subtitle, target.rel_path),
            )

    def resolve_file(self, user: User, media_file_id: int, user_agent: str) -> PlaybackTarget:
        """给已通过 WebDAV Basic Auth 的用户按请求 UA 解析可见文件。"""
        with self._database.read_session() as connection:
            library_ids = visible_library_ids(connection, user)
            scope = ", ".join("?" for _ in library_ids) or "NULL"
            row = connection.execute(
                f"""
                SELECT c.playback_locator, c.folder_id
                FROM media_file c JOIN library_folder f ON f.id = c.folder_id
                WHERE c.id = ? AND c.state = 'active' AND c.playback_locator IS NOT NULL
                    AND f.library_id IN ({scope})
                """,
                (media_file_id, *library_ids),
            ).fetchone()
            if row is None:
                raise PlayDenied(404, "文件不存在或已失效")
            source = ResourceSources.folder(connection, row["folder_id"])
        return self._resolve(source, str(row["playback_locator"]), user_agent, f"webdav:{user.id}")

    def _resolve(
        self, source: FolderSource | None, locator: str, user_agent: str, throttle_key: str
    ) -> PlaybackTarget:
        """按文件所在来源解析，缓存和限流由来源插件自行处理。"""
        if source is not None and not source.enabled:
            raise PlayDenied(503, f"{source.disabled_reason()}，请联系管理员")
        if source is None:
            raise PlayDenied(503, "文件来源不存在，请联系管理员")
        wait = self._throttle.request(throttle_key)
        if wait:
            raise PlayDenied(429, "播放请求过于频繁，请稍后再试", retry_after=wait)
        try:
            target = self._resources.open(source).resolve(locator, user_agent, throttle_key)
        except ResourceNotFound as exc:
            raise PlayDenied(404, exc.message or "文件不存在或已失效") from None
        except ResourceRateLimited as exc:
            raise PlayDenied(exc.status_code, exc.message, exc.retry_after) from None
        except ResourceUnavailable as exc:
            raise PlayDenied(503, exc.message) from None
        return self._local_path(target)

    def _local_path(self, target: PlaybackTarget) -> PlaybackTarget:
        """插件给出的本地路径必须是 media_dir 内的真实文件；越界和符号链接都不接受。"""
        if target.path is None:
            return target
        try:
            path = Path(target.path).resolve(strict=True)
        except OSError:
            raise PlayDenied(404, "文件不存在或已失效") from None
        if (path != self._media_dir and self._media_dir not in path.parents) or not path.is_file():
            raise PlayDenied(404, "文件不存在或已失效")
        return replace(target, path=path)


def launch_links(url: str, title: str, subtitle_url: str | None) -> LaunchLinks:
    """各播放器的调起链接，格式依据 ADR 0012 的调研，真机实测前可能还要调整。"""
    sub = f"&sub={quote(subtitle_url, safe='')}" if subtitle_url else ""
    encoded = quote(url, safe="")
    parts = urlsplit(url)
    return LaunchLinks(
        infuse=f"infuse://x-callback-url/play?url={encoded}{sub}",
        vlc=f"vlc-x-callback://x-callback-url/stream?url={encoded}{sub}",
        # nPlayer 在原地址前加前缀，没有外挂字幕参数
        nplayer=f"nplayer-{url}",
        senplayer=f"senplayer://x-callback-url/play?url={encoded}",
        # Android 系统选择器：按视频类型列出能打开它的应用
        android=(
            f"intent://{parts.netloc}{parts.path}#Intent;scheme={parts.scheme};"
            f"type=video/*;S.title={quote(title, safe='')};end"
        ),
    )


def _target(connection: sqlite3.Connection, user: User, version_id: int) -> _Target | None:
    """可见范围内、文件可用的版本。"""
    library_ids = visible_library_ids(connection, user)
    scope = ", ".join("?" for _ in library_ids) or "NULL"
    row = connection.execute(
        f"""
        SELECT v.id, v.playable_id, c.rel_path, c.playback_locator, c.folder_id, m.title,
            s.number AS season_number, e.number AS episode_number
        FROM version v JOIN media_file c ON c.id = v.media_file_id
        JOIN playable p ON p.id = v.playable_id
        JOIN media_item m ON m.id = p.media_item_id
        LEFT JOIN episode e ON e.id = p.episode_id
        LEFT JOIN season s ON s.id = e.season_id
        WHERE v.id = ? AND m.library_id IN ({scope})
            AND c.state = 'active' AND c.playback_locator IS NOT NULL
        """,
        (version_id, *library_ids),
    ).fetchone()
    if row is None:
        return None
    title = row["title"]
    if row["episode_number"] is not None:
        title = f"{title} S{row['season_number']:02d}E{row['episode_number']:02d}"
    return _Target(
        row["id"],
        row["playable_id"],
        title,
        row["rel_path"],
        row["playback_locator"],
        row["folder_id"],
    )


def _subtitles(
    connection: sqlite3.Connection, version_id: int, user_id: int | None = None
) -> list[sqlite3.Row]:
    """版本可用的字幕（外挂和在线）。默认字幕在前，其次是偏好的语言和中文。

    偏好里的 zh-CN/zh-TW 要先统一成字幕使用的 zh-Hans/zh-Hant；资料库默认字幕语言也从
    这个版本所属的条目反查（以前误把 version_id 当成 media_item_id）。
    """
    user_preference = None
    if user_id is not None:
        row = connection.execute(
            "SELECT subtitle_language FROM user_playback_preference WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        user_preference = row["subtitle_language"] if row is not None else None
    library_default = connection.execute(
        """
        SELECT st.value
        FROM setting st
        WHERE st.key = 'library.' || (
            SELECT m.library_id
            FROM version v JOIN playable p ON p.id = v.playable_id
            JOIN media_item m ON m.id = p.media_item_id
            WHERE v.id = ?
        ) || '.subtitle_language'
        LIMIT 1
        """,
        (version_id,),
    ).fetchone()
    preferred = normalize_language_code(user_preference) or normalize_language_code(
        library_default["value"] if library_default is not None else None
    )
    rows = _subtitle_rows(connection, version_id)

    def order(row: sqlite3.Row) -> tuple[int, int, int, int, int, int]:
        language = row["language"]
        return (
            0 if preferred is not None and language == preferred else 1,
            0 if row["is_default"] else 1,
            0 if not row["forced"] else 1,
            0 if isinstance(language, str) and language.startswith("zh") else 1,
            0 if row["source"] == "external" else 1,
            int(row["id"]),
        )

    return sorted(rows, key=order)


def _subtitle_rows(
    connection: sqlite3.Connection,
    version_id: int,
    subtitle_id: int | None = None,
) -> list[sqlite3.Row]:
    condition = "AND s.id = ?" if subtitle_id is not None else ""
    params: tuple[int, ...] = (
        (version_id, subtitle_id) if subtitle_id is not None else (version_id,)
    )
    return connection.execute(
        f"""
        SELECT s.id, s.label, s.language, s.forced, s.is_default, s.source,
            c.rel_path, c.playback_locator, c.folder_id, o.format
        FROM subtitle s
        LEFT JOIN media_file c ON c.id = s.media_file_id
        LEFT JOIN online_subtitle o ON o.subtitle_id = s.id
        WHERE s.version_id = ?
            AND (s.source = 'online' OR (c.state = 'active' AND c.playback_locator IS NOT NULL))
            {condition}
        """,
        params,
    ).fetchall()


def _subtitle_row(
    connection: sqlite3.Connection, version_id: int, subtitle_id: int
) -> sqlite3.Row | None:
    rows = _subtitle_rows(connection, version_id, subtitle_id)
    return rows[0] if rows else None


def _subtitle_filename(subtitle: sqlite3.Row, video_rel_path: str) -> str:
    """外挂字幕用来源上的文件名；在线字幕合成「视频名.语言.格式」。"""
    if subtitle["source"] != "online":
        return _basename(str(subtitle["rel_path"]))
    stem = _basename(video_rel_path).rpartition(".")[0] or _basename(video_rel_path)
    language = _safe_filename_part(str(subtitle["language"] or "und"))
    format = _safe_filename_part(str(subtitle["format"] or "srt"))
    return f"{stem}.{language}.{format}"


def _safe_filename_part(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_")


def _online_content(connection: sqlite3.Connection, subtitle_id: int) -> bytes:
    row = connection.execute(
        "SELECT content FROM online_subtitle WHERE subtitle_id = ?", (subtitle_id,)
    ).fetchone()
    if row is None:
        raise PlayDenied(404, "字幕内容不存在")
    try:
        return decompress(bytes(row["content"]))
    except AppError as exc:
        raise PlayDenied(404, exc.message) from None


def _play_path(token: str, filename: str) -> str:
    # 文件名放在路径末尾，播放器靠它识别扩展名
    return f"/play/{token}/{quote(filename)}"


def _basename(rel_path: str) -> str:
    return rel_path.rpartition("/")[2]


def _valid_claims(claims: dict[str, Any]) -> bool:
    """必需字段和可选的字幕 ID 都是整数。签名对得上时只有密钥泄露才会不满足，仍然防一手。"""
    keys = ("sub", "tv", "v", "exp", *(("s",) if "s" in claims else ()))
    return all(type(claims.get(key)) is int for key in keys)
