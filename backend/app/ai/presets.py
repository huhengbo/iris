"""新建模型提供方时可选的预设；只是预填，管理员可以改成任何地址。"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.types import ProviderKind


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    kind: ProviderKind
    base_url: str
    # 预填的模型名，按用途；没有的用途留空
    chat_model: str = ""
    embedding_model: str = ""


PRESETS: tuple[Preset, ...] = (
    Preset(
        "deepseek", "DeepSeek", "openai", "https://api.deepseek.com", chat_model="deepseek-chat"
    ),
    Preset(
        "siliconflow",
        "硅基流动",
        "openai",
        "https://api.siliconflow.cn/v1",
        embedding_model="BAAI/bge-m3",
    ),
    Preset("anthropic", "Anthropic", "anthropic", "https://api.anthropic.com"),
    Preset("openai", "OpenAI", "openai", "https://api.openai.com/v1"),
    Preset("ollama", "Ollama", "openai", "http://127.0.0.1:11434/v1"),
)
