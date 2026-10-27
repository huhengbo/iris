"""账号页和凭据：Cookie、扫码登录、两组熔断状态和失效处理。

从 backend/tests/test_cloud115.py 迁移；凭据不再加密，改为插件自己的 0600 明文状态文件。
"""

from __future__ import annotations

import base64
import stat
from datetime import timedelta

import pytest
from iris_plugin_sdk import PluginRequestError

from iris_source_115.errors import Cloud115Unavailable, RateLimited
from iris_source_115.gateway import QrStatus
from tests.conftest import Harness, restart, save_cookie, status_body
from tests.fake_gateway import ACCOUNT, QR_COOKIE, QR_IMAGE, VALID_COOKIE

SECRET_PARTS = ["cid-secret-value", "seid-secret-value", "qr-cid-secret", "qr-seid-secret"]


def error_code(exc: PluginRequestError) -> str:
    return exc.code


def assert_no_secret(text: str) -> None:
    for part in SECRET_PARTS:
        assert part not in text


def test_status_before_any_account_is_connected(harness: Harness) -> None:
    body = status_body(harness)

    assert body["credential"] == {
        "state": "missing",
        "device": None,
        "device_label": None,
        "user_name": None,
        "saved_at": None,
        "invalid_at": None,
    }
    assert body["rate_limit"] == {
        "limited": False,
        "resume_at": None,
        "apis": [
            {"id": "web", "label": "网页版接口", "limited": False, "resume_at": None},
            {"id": "app", "label": "App 版接口", "limited": False, "resume_at": None},
        ],
    }
    with pytest.raises(PluginRequestError) as error:
        harness.request("GET", "account")
    assert error.value.status == 409
    assert error_code(error.value) == "cloud115_not_configured"


def test_pasted_cookie_is_validated_then_saved_to_a_private_state_file(
    harness: Harness,
) -> None:
    response = save_cookie(harness, f"Cookie: {VALID_COOKIE}\n")

    assert response.status == 200
    assert response.body["user_name"] == ACCOUNT.user_name
    assert harness.gateway.calls == ["account_info"]
    credential = status_body(harness)["credential"]
    assert credential["state"] == "valid"
    # 设备身份从 Cookie 的 UID（R2）推断
    assert credential["device"] == "alipaymini"
    assert credential["device_label"] == "支付宝小程序"
    assert credential["user_name"] == ACCOUNT.user_name
    account = harness.request("GET", "account").body
    assert account["space_used"] == ACCOUNT.space_used

    path = harness.state_dir / "credential.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    stored = harness.state("credential.json")
    assert stored["cookie"] == VALID_COOKIE
    assert stored["device"] == "alipaymini"
    assert stored["user_name"] == ACCOUNT.user_name
    assert stored["invalid_at"] is None


def test_rejected_cookie_is_not_saved(harness: Harness) -> None:
    with pytest.raises(PluginRequestError) as error:
        save_cookie(harness, "UID=1_R2_1; CID=stale; SEID=stale")

    assert error.value.status == 409
    assert error_code(error.value) == "cloud115_credential_invalid"
    assert status_body(harness)["credential"]["state"] == "missing"
    assert not (harness.state_dir / "credential.json").exists()


def test_malformed_cookie_is_rejected_without_calling_115(harness: Harness) -> None:
    with pytest.raises(PluginRequestError) as error:
        save_cookie(harness, "not a cookie")

    assert error.value.status == 422
    assert error_code(error.value) == "invalid_cookie"
    assert harness.gateway.calls == []


def test_risk_control_opens_breaker_then_recovers_after_backoff(harness: Harness) -> None:
    assert save_cookie(harness).status == 200
    harness.gateway.error = RateLimited()

    with pytest.raises(PluginRequestError) as first:
        harness.request("GET", "account")
    assert first.value.status == 429
    assert first.value.code == "resource_rate_limited"
    calls = len(harness.gateway.calls)

    # 熔断期间立即失败，不再请求 115；风控不等于凭据失效
    with pytest.raises(PluginRequestError) as again:
        harness.request("GET", "account")
    assert again.value.status == 429
    assert len(harness.gateway.calls) == calls
    body = status_body(harness)
    assert body["credential"]["state"] == "valid"
    resume_at = (harness.clock.now() + timedelta(minutes=30)).isoformat()
    assert body["rate_limit"] == {
        "limited": True,
        "resume_at": resume_at,
        "apis": [
            {"id": "web", "label": "网页版接口", "limited": True, "resume_at": resume_at},
            {"id": "app", "label": "App 版接口", "limited": False, "resume_at": None},
        ],
    }

    harness.clock.advance(minutes=29)
    with pytest.raises(PluginRequestError) as still_limited:
        harness.request("GET", "account")
    assert still_limited.value.status == 429
    assert len(harness.gateway.calls) == calls

    harness.gateway.error = None
    harness.clock.advance(minutes=1)
    assert harness.request("GET", "account").status == 200
    assert status_body(harness)["rate_limit"]["limited"] is False


def test_repeated_risk_control_doubles_the_backoff(harness: Harness) -> None:
    assert save_cookie(harness).status == 200
    harness.gateway.error = RateLimited()
    with pytest.raises(PluginRequestError):
        harness.request("GET", "account")
    harness.clock.advance(minutes=30)

    # 恢复后的第一次请求又被拦，退避翻倍
    with pytest.raises(PluginRequestError):
        harness.request("GET", "account")
    resume_at = status_body(harness)["rate_limit"]["resume_at"]
    assert resume_at == (harness.clock.now() + timedelta(minutes=60)).isoformat()


def test_breaker_survives_restart(harness: Harness) -> None:
    assert save_cookie(harness).status == 200
    harness.gateway.error = RateLimited()
    with pytest.raises(PluginRequestError):
        harness.request("GET", "account")

    fresh = restart(harness)
    try:
        with pytest.raises(PluginRequestError) as error:
            fresh.request("GET", "account")
        assert error.value.status == 429
        # 重启后熔断仍然有效，不请求 115
        assert fresh.gateway.calls == []
        assert status_body(fresh)["rate_limit"]["limited"] is True
        # 熔断状态写在插件自己的状态目录里
        stored = fresh.state("breaker-web.json")
        assert stored["level"] == 1
        assert stored["resume_at"] is not None
    finally:
        fresh.plugin.stop()


def test_temporary_errors_do_not_open_the_breaker(harness: Harness) -> None:
    assert save_cookie(harness).status == 200
    harness.gateway.error = Cloud115Unavailable()

    with pytest.raises(PluginRequestError) as error:
        harness.request("GET", "account")
    assert error.value.status == 400
    harness.gateway.error = None
    assert harness.request("GET", "account").status == 200
    assert status_body(harness)["rate_limit"]["limited"] is False


def test_rejected_stored_credential_is_flagged_until_replaced(harness: Harness) -> None:
    assert save_cookie(harness).status == 200
    # 模拟在别处被挤下线
    harness.gateway.valid_cookies.discard(VALID_COOKIE)

    with pytest.raises(PluginRequestError) as error:
        harness.request("GET", "account")
    assert error.value.status == 409
    assert error_code(error.value) == "cloud115_credential_invalid"
    credential = status_body(harness)["credential"]
    assert credential["state"] == "invalid"
    assert credential["invalid_at"] is not None

    # 已知失效的凭据不再拿去请求 115
    calls = len(harness.gateway.calls)
    with pytest.raises(PluginRequestError):
        harness.request("GET", "account")
    assert len(harness.gateway.calls) == calls

    assert save_cookie(harness, QR_COOKIE).status == 200
    assert status_body(harness)["credential"]["state"] == "valid"


def test_qr_login_saves_the_credential_for_the_chosen_device(harness: Harness) -> None:
    devices = harness.request("GET", "devices").body
    assert devices[0] == {
        "id": "alipaymini",
        "label": "支付宝小程序",
        "recommended": True,
        "note": None,
    }
    assert not next(d for d in devices if d["id"] == "web")["recommended"]

    started = harness.request("POST", "qr-login", {"device": "tv"})
    assert started.status == 201
    session = started.body
    assert base64.b64decode(session["image_base64"]) == QR_IMAGE
    poll = f"qr-login/{session['session_id']}"

    harness.gateway.qr_statuses = [QrStatus.WAITING, QrStatus.SCANNED, QrStatus.CONFIRMED]
    assert harness.request("GET", poll).body["status"] == "waiting"
    assert harness.request("GET", poll).body["status"] == "scanned"
    done = harness.request("GET", poll)
    assert done.body["status"] == "succeeded"
    assert done.body["account"]["user_name"] == ACCOUNT.user_name
    assert_no_secret(str(done.body))

    credential = status_body(harness)["credential"]
    assert credential["state"] == "valid"
    assert credential["device"] == "tv"
    # 凭据只换取一次，会话随即结束
    with pytest.raises(PluginRequestError) as error:
        harness.request("GET", poll)
    assert error.value.status == 404


def test_qr_session_expires(harness: Harness) -> None:
    session = harness.request("POST", "qr-login", {"device": "alipaymini"}).body
    harness.clock.advance(minutes=3)

    response = harness.request("GET", f"qr-login/{session['session_id']}").body
    assert response["status"] == "expired"
    assert "qr_status" not in harness.gateway.calls
    assert status_body(harness)["credential"]["state"] == "missing"


def test_cancelled_qr_login(harness: Harness) -> None:
    session = harness.request("POST", "qr-login", {"device": "alipaymini"}).body
    poll = f"qr-login/{session['session_id']}"

    assert harness.request("DELETE", poll).status == 204
    with pytest.raises(PluginRequestError) as error:
        harness.request("GET", poll)
    assert error.value.status == 404


def test_qr_login_rejects_unknown_device(harness: Harness) -> None:
    with pytest.raises(PluginRequestError) as error:
        harness.request("POST", "qr-login", {"device": "desktop"})

    assert error.value.status == 422
    assert harness.gateway.calls == []


def test_credential_never_appears_in_responses(harness: Harness) -> None:
    texts = [str(save_cookie(harness).body)]
    texts.append(str(status_body(harness)))
    texts.append(str(harness.request("GET", "account").body))
    session = harness.request("POST", "qr-login", {"device": "tv"}).body
    texts.append(str(session))
    harness.gateway.qr_statuses = [QrStatus.CONFIRMED]
    texts.append(str(harness.request("GET", f"qr-login/{session['session_id']}").body))
    texts.append(str(status_body(harness)))

    for text in texts:
        assert_no_secret(text)
    # 只有凭据文件里有明文，其他状态文件都不带 Cookie；所有文件都是 0600
    for path in harness.state_dir.iterdir():
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, path
        content = path.read_text(encoding="utf-8")
        if path.name == "credential.json":
            # 最后一次是扫码登录，凭据文件里是扫码换来的 Cookie
            assert QR_COOKIE in content
        else:
            for part in SECRET_PARTS:
                assert part not in content


def test_deleting_the_credential(harness: Harness) -> None:
    assert save_cookie(harness).status == 200

    assert harness.request("DELETE", "credential").status == 204
    assert status_body(harness)["credential"]["state"] == "missing"
    assert not (harness.state_dir / "credential.json").exists()
