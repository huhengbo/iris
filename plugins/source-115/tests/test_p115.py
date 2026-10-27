"""p115client 错误翻译：只收拢已知业务/网络异常，程序错误照常冒泡。"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from p115client.exception import P115OperationalError

from iris_source_115.errors import Cloud115Unavailable
from iris_source_115.p115 import P115Gateway, _translate_errors


def test_known_115_business_error_becomes_unavailable() -> None:
    with pytest.raises(Cloud115Unavailable), _translate_errors():
        raise P115OperationalError("115 返回了业务错误")


def test_unexpected_programming_error_bubbles() -> None:
    with pytest.raises(RuntimeError, match="bug"), _translate_errors():
        raise RuntimeError("bug")


def test_gateway_uses_the_configured_proxy_session(
    real_gateway: Callable[..., P115Gateway],
) -> None:
    from urllib3_future import PoolManager, ProxyManager
    from urllib3_future.contrib.socks import SOCKSProxyManager

    direct = real_gateway()
    assert type(direct._session) is PoolManager

    http = real_gateway("http://127.0.0.1:7890")
    assert isinstance(http._session, ProxyManager)
    assert http._http()["session"] is http._session

    socks = real_gateway("socks5://127.0.0.1:1080")
    assert isinstance(socks._session, SOCKSProxyManager)

    socks.configure(None)
    assert type(socks._session) is PoolManager
