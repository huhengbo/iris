"""插件测试用的假宿主 HTTP：按 PluginHttpClient 接口记录请求并返回构造好的响应。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from iris_plugin_sdk.types import PluginHttpResponse


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    params: Mapping[str, Any]
    headers: Mapping[str, str]

    @property
    def path(self) -> str:
        return urlsplit(self.url).path


def response(
    status: int = 200,
    *,
    json_body: Any = None,
    text: str | None = None,
    content: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    url: str = "https://example.test/",
) -> PluginHttpResponse:
    """构造插件 HTTP 响应；只设置一种正文，headers 在默认值上覆盖。"""
    if json_body is not None:
        body = json.dumps(json_body).encode()
    elif text is not None:
        body = text.encode()
    else:
        body = content or b""
    merged = {"content-type": "application/json", **dict(headers or {})}
    return PluginHttpResponse(status_code=status, headers=merged, content=body, url=url)


class FakeHttp:
    def __init__(self, handler: Callable[[RecordedRequest], PluginHttpResponse]) -> None:
        self.requests: list[RecordedRequest] = []
        self._handler = handler

    def request(self, method: str, url: str, **kwargs: Any) -> PluginHttpResponse:
        recorded = RecordedRequest(
            method=method,
            url=url,
            params={str(key): str(value) for key, value in (kwargs.get("params") or {}).items()},
            headers={
                str(key).lower(): str(value) for key, value in (kwargs.get("headers") or {}).items()
            },
        )
        self.requests.append(recorded)
        return self._handler(recorded)

    def get(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> PluginHttpResponse:
        return self.request("DELETE", url, **kwargs)
