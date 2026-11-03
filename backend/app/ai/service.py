"""模型提供方、用途选择和用量记录；所有模型调用都从这里发出（ADR 0030）。"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.ai.anthropic_client import AnthropicClient
from app.ai.openai_client import OpenAiClient
from app.ai.types import (
    PROVIDER_KINDS,
    ROLES,
    AiError,
    Embeddings,
    Finish,
    Message,
    ModelClient,
    ProviderConfig,
    ProviderKind,
    Role,
    StreamEvent,
    ToolSpec,
    UsageEvent,
)
from app.clock import Clock, to_iso
from app.crypto import SecretBox
from app.db import Database
from app.errors import AppError, NotFound

ClientFactory = Callable[[ProviderConfig], ModelClient]

MAX_NAME_LENGTH = 64
MAX_URL_LENGTH = 512
MAX_KEY_LENGTH = 1024
MAX_MODEL_LENGTH = 128
DEFAULT_MAX_TOKENS = 4096
TEST_MAX_TOKENS = 16


def default_client_factory(config: ProviderConfig) -> ModelClient:
    if config.kind == "anthropic":
        return AnthropicClient(config)
    return OpenAiClient(config)


@dataclass(frozen=True)
class TestResult:
    ok: bool
    checked_at: str
    duration_ms: int
    error_kind: str | None = None
    message: str | None = None

    # 不是 pytest 用例，避免被收集
    __test__ = False


@dataclass(frozen=True)
class Provider:
    id: int
    name: str
    kind: ProviderKind
    base_url: str
    key_set: bool
    last_test: TestResult | None
    used_by: tuple[Role, ...]


@dataclass(frozen=True)
class RoleBinding:
    provider_id: int
    model: str


@dataclass(frozen=True)
class UsageRow:
    month: str
    role: Role
    calls: int
    failures: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Availability:
    """客户端据此决定显示哪些 AI 入口。"""

    chat: bool
    ask_search: bool
    semantic: bool


def clean_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise AppError(400, "invalid_ai_provider", f"名称不能为空，最多 {MAX_NAME_LENGTH} 个字")
    return name


def clean_base_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.query
        or parts.fragment
        or any(c.isspace() for c in url)
        or len(url) > MAX_URL_LENGTH
    ):
        raise AppError(
            400,
            "invalid_ai_provider",
            "接口地址应以 http:// 或 https:// 开头，例如 https://api.deepseek.com",
        )
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def clean_api_key(key: str | None) -> str | None:
    key = (key or "").strip()
    if not key:
        return None
    if any(c.isspace() for c in key) or len(key) > MAX_KEY_LENGTH:
        raise AppError(400, "invalid_ai_provider", "Key 格式不正确")
    return key


def clean_model(model: str) -> str:
    model = model.strip()
    if not model or any(c.isspace() for c in model) or len(model) > MAX_MODEL_LENGTH:
        raise AppError(400, "invalid_ai_role", "模型名不能为空，也不能包含空格")
    return model


class AiService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        box: SecretBox,
        factory: ClientFactory = default_client_factory,
    ) -> None:
        self._database = database
        self._clock = clock
        self._box = box
        self._factory = factory
        # 语义召回能否使用还取决于 sqlite-vec 是否加载成功（M55 接入）
        self.vector_search_available: Callable[[], bool] = lambda: False

    # 提供方 ---------------------------------------------------------------

    def list_providers(self) -> list[Provider]:
        with self._database.read_session() as connection:
            roles = _role_rows(connection)
            rows = connection.execute("SELECT * FROM ai_provider ORDER BY id").fetchall()
            return [_provider(row, roles) for row in rows]

    def get_provider(self, provider_id: int) -> Provider:
        with self._database.read_session() as connection:
            row = _provider_row(connection, provider_id)
            return _provider(row, _role_rows(connection))

    def create_provider(
        self, name: str, kind: ProviderKind, base_url: str, api_key: str | None
    ) -> Provider:
        name = clean_name(name)
        base_url = clean_base_url(base_url)
        key = clean_api_key(api_key)
        _check_kind(kind)
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            _ensure_name_free(connection, name, None)
            cursor = connection.execute(
                "INSERT INTO ai_provider (name, kind, base_url, api_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, kind, base_url, self._box.encrypt(key) if key else None, now, now),
            )
            provider_id = int(cursor.lastrowid or 0)
        return self.get_provider(provider_id)

    def update_provider(
        self,
        provider_id: int,
        *,
        name: str,
        kind: ProviderKind,
        base_url: str,
        api_key: str | None,
        change_key: bool,
    ) -> Provider:
        """change_key 为 False 时保持原 Key；为 True 时 api_key 为空表示清除。"""
        name = clean_name(name)
        base_url = clean_base_url(base_url)
        key = clean_api_key(api_key)
        _check_kind(kind)
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            row = _provider_row(connection, provider_id)
            _ensure_name_free(connection, name, provider_id)
            if (
                kind != row["kind"]
                and kind == "anthropic"
                and _used_for_embedding(connection, provider_id)
            ):
                raise AppError(
                    400, "invalid_ai_provider", "该提供方正用作向量模型，不能改成 Anthropic"
                )
            encrypted = row["api_key"]
            if change_key:
                encrypted = self._box.encrypt(key) if key else None
            connection.execute(
                "UPDATE ai_provider SET name = ?, kind = ?, base_url = ?, api_key = ?, "
                "last_test_ok = NULL, last_test_error = NULL, last_tested_at = NULL, "
                "updated_at = ? WHERE id = ?",
                (name, kind, base_url, encrypted, now, provider_id),
            )
        return self.get_provider(provider_id)

    def delete_provider(self, provider_id: int) -> None:
        with self._database.session() as connection:
            _provider_row(connection, provider_id)
            if connection.execute(
                "SELECT 1 FROM ai_model_role WHERE provider_id = ?", (provider_id,)
            ).fetchone():
                raise AppError(409, "ai_provider_in_use", "该提供方正被某个用途使用，请先改用途")
            connection.execute("DELETE FROM ai_provider WHERE id = ?", (provider_id,))

    def test_provider(self, provider_id: int) -> TestResult:
        """发一次最小请求（列出模型），记录结果。"""
        with self._database.read_session() as connection:
            config = self._config(_provider_row(connection, provider_id))
        result = self._run_test(lambda: self._factory(config).ping())
        with self._database.session() as connection:
            connection.execute(
                "UPDATE ai_provider SET last_test_ok = ?, last_test_error = ?, "
                "last_tested_at = ? WHERE id = ?",
                (int(result.ok), result.message, result.checked_at, provider_id),
            )
        return result

    # 用途 -----------------------------------------------------------------

    def roles(self) -> dict[Role, RoleBinding | None]:
        with self._database.read_session() as connection:
            rows = _role_rows(connection)
        return {role: rows.get(role) for role in ROLES}

    def set_role(self, role: Role, binding: RoleBinding | None) -> None:
        now = to_iso(self._clock.now())
        with self._database.session() as connection:
            if binding is None:
                connection.execute("DELETE FROM ai_model_role WHERE role = ?", (role,))
                return
            model = clean_model(binding.model)
            row = connection.execute(
                "SELECT kind FROM ai_provider WHERE id = ?", (binding.provider_id,)
            ).fetchone()
            if row is None:
                raise AppError(400, "invalid_ai_role", "所选的模型提供方不存在")
            if role == "embedding" and row["kind"] != "openai":
                raise AppError(400, "invalid_ai_role", "向量模型需要 OpenAI 兼容的提供方")
            connection.execute(
                "INSERT INTO ai_model_role (role, provider_id, model, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (role) DO UPDATE SET "
                "provider_id = excluded.provider_id, model = excluded.model, "
                "updated_at = excluded.updated_at",
                (role, binding.provider_id, model, now),
            )

    def test_role(self, role: Role) -> TestResult:
        """用这个用途选的模型发一次最小请求，确认模型名可用；不计入用量。"""

        def call() -> None:
            config, model = self._resolve(role)
            client = self._factory(config)
            if role == "embedding":
                client.embed(model=model, texts=["ping"])
            else:
                for _ in client.stream(
                    model=model,
                    messages=[Message("user", "ping")],
                    tools=[],
                    max_tokens=TEST_MAX_TOKENS,
                ):
                    pass

        return self._run_test(call)

    def availability(self) -> Availability:
        bound = {role for role, binding in self.roles().items() if binding is not None}
        chat = "chat" in bound
        return Availability(
            chat=chat,
            ask_search=chat,
            # 语义召回服务于 AI 找片，没有对话模型时没有意义
            semantic=chat and "embedding" in bound and self.vector_search_available(),
        )

    # 调用 -----------------------------------------------------------------

    def stream(
        self,
        role: Role,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        user_id: int | None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[StreamEvent]:
        """流式调用一个用途的模型，结束（或出错、被中断）时记一条用量。"""
        config, model = self._resolve(role)
        started = time.monotonic()
        input_tokens = output_tokens = 0
        finished = False
        try:
            for event in self._factory(config).stream(
                model=model, messages=messages, tools=tools, max_tokens=max_tokens
            ):
                if isinstance(event, UsageEvent):
                    input_tokens, output_tokens = event.input_tokens, event.output_tokens
                elif isinstance(event, Finish):
                    finished = True
                yield event
        finally:
            self._record_usage(
                role, user_id, config.id, model, input_tokens, output_tokens, started, finished
            )

    def embed(self, texts: list[str], *, user_id: int | None = None) -> Embeddings:
        config, model = self._resolve("embedding")
        started = time.monotonic()
        tokens = 0
        ok = False
        try:
            result = self._factory(config).embed(model=model, texts=texts)
            tokens = result.input_tokens
            ok = True
            return result
        finally:
            self._record_usage("embedding", user_id, config.id, model, tokens, 0, started, ok)

    # 用量 -----------------------------------------------------------------

    def usage(self, months: int) -> list[UsageRow]:
        """最近若干个月（含本月）按月份和用途汇总，新的月份在前。"""
        now = self._clock.now()
        index = now.year * 12 + now.month - 1 - (months - 1)
        since = f"{index // 12:04d}-{index % 12 + 1:02d}"
        with self._database.read_session() as connection:
            rows = connection.execute(
                """
                SELECT substr(created_at, 1, 7) AS month, role, COUNT(*) AS calls,
                    SUM(1 - ok) AS failures, SUM(input_tokens) AS input_tokens,
                    SUM(output_tokens) AS output_tokens
                FROM ai_usage WHERE substr(created_at, 1, 7) >= ?
                GROUP BY month, role ORDER BY month DESC, role
                """,
                (since,),
            ).fetchall()
        return [
            UsageRow(
                month=r["month"],
                role=r["role"],
                calls=r["calls"],
                failures=r["failures"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
            )
            for r in rows
        ]

    # 内部 -----------------------------------------------------------------

    def _resolve(self, role: Role) -> tuple[ProviderConfig, str]:
        with self._database.read_session() as connection:
            row = connection.execute(
                "SELECT p.*, r.model AS model FROM ai_model_role r "
                "JOIN ai_provider p ON p.id = r.provider_id WHERE r.role = ?",
                (role,),
            ).fetchone()
            if row is None:
                raise AiError("not_configured", "还没有为这个用途选择模型")
            return self._config(row), str(row["model"])

    def _config(self, row: sqlite3.Row) -> ProviderConfig:
        key = None
        if row["api_key"]:
            key = self._box.decrypt(row["api_key"])
            if key is None:
                raise AiError("auth", "Key 无法解密（应用密钥已更换），请重新填写")
        return ProviderConfig(id=row["id"], kind=row["kind"], base_url=row["base_url"], api_key=key)

    def _run_test(self, call: Callable[[], None]) -> TestResult:
        started = time.monotonic()
        error: AiError | None = None
        try:
            call()
        except AiError as exc:
            error = exc
        return TestResult(
            ok=error is None,
            checked_at=to_iso(self._clock.now()),
            duration_ms=int((time.monotonic() - started) * 1000),
            error_kind=error.kind if error else None,
            message=error.message if error else None,
        )

    def _record_usage(
        self,
        role: Role,
        user_id: int | None,
        provider_id: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        started: float,
        ok: bool,
    ) -> None:
        with self._database.session() as connection:
            connection.execute(
                "INSERT INTO ai_usage (created_at, role, user_id, provider_id, model, "
                "input_tokens, output_tokens, duration_ms, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    to_iso(self._clock.now()),
                    role,
                    user_id,
                    provider_id,
                    model,
                    input_tokens,
                    output_tokens,
                    int((time.monotonic() - started) * 1000),
                    int(ok),
                ),
            )


def _check_kind(kind: str) -> None:
    if kind not in PROVIDER_KINDS:
        raise AppError(400, "invalid_ai_provider", "提供方类型应为 openai 或 anthropic")


def _provider_row(connection: sqlite3.Connection, provider_id: int) -> sqlite3.Row:
    row: sqlite3.Row | None = connection.execute(
        "SELECT * FROM ai_provider WHERE id = ?", (provider_id,)
    ).fetchone()
    if row is None:
        raise NotFound("模型提供方不存在")
    return row


def _ensure_name_free(connection: sqlite3.Connection, name: str, own_id: int | None) -> None:
    row = connection.execute("SELECT id FROM ai_provider WHERE name = ?", (name,)).fetchone()
    if row is not None and row["id"] != own_id:
        raise AppError(409, "ai_provider_name_taken", "已经有同名的模型提供方")


def _used_for_embedding(connection: sqlite3.Connection, provider_id: int) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM ai_model_role WHERE role = 'embedding' AND provider_id = ?",
            (provider_id,),
        ).fetchone()
        is not None
    )


def _role_rows(connection: sqlite3.Connection) -> dict[Role, RoleBinding]:
    return {
        r["role"]: RoleBinding(r["provider_id"], r["model"])
        for r in connection.execute("SELECT role, provider_id, model FROM ai_model_role")
    }


def _provider(row: sqlite3.Row, roles: dict[Role, RoleBinding]) -> Provider:
    last_test = None
    if row["last_tested_at"] is not None:
        last_test = TestResult(
            ok=bool(row["last_test_ok"]),
            checked_at=row["last_tested_at"],
            duration_ms=0,
            message=row["last_test_error"],
        )
    return Provider(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        base_url=row["base_url"],
        key_set=bool(row["api_key"]),
        last_test=last_test,
        used_by=tuple(
            role for role in ROLES if (b := roles.get(role)) and b.provider_id == row["id"]
        ),
    )
