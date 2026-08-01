"""Webhook 簽章驗證測試。

這是唯一擋在外部與資料庫之間的防線，必須測到。
"""

import base64
import hashlib
import hmac

import pytest

from src.config import get_settings
from src.webhook.app import _verify_signature

_SECRET = "test_channel_secret"
_BODY = b'{"events":[]}'


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch: pytest.MonkeyPatch):
    """注入測試用 channel secret，並清掉設定快取避免測試互相污染。"""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", _SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_正確簽章通過驗證() -> None:
    assert _verify_signature(_BODY, _sign(_BODY, _SECRET)) is True


def test_錯誤簽章被拒絕() -> None:
    assert _verify_signature(_BODY, _sign(_BODY, "wrong_secret")) is False


def test_body_被竄改後簽章失效() -> None:
    signature = _sign(_BODY, _SECRET)
    tampered = b'{"events":[{"type":"follow"}]}'
    assert _verify_signature(tampered, signature) is False


def test_缺少簽章_header_被拒絕() -> None:
    assert _verify_signature(_BODY, "") is False


def test_未設定_secret_時一律拒絕(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "")
    get_settings.cache_clear()
    assert _verify_signature(_BODY, _sign(_BODY, _SECRET)) is False
