"""Webhook 端點行為測試（簽章演算法本身見 test_webhook_signature.py）。

這裡驗的是端點的對外契約：什麼情況回幾號、事件怎麼分派。
特別重要的是「處理失敗仍回 200」—— 回 500 會讓 LINE 不斷重送同一批事件。
"""

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.config import get_settings
from src.webhook.app import app

_SECRET = "test_channel_secret"


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", _SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """攔下事件分派，記錄收到哪些事件。"""
    received: list[dict] = []

    async def fake_handle(event: dict) -> None:
        received.append(event)

    monkeypatch.setattr("src.webhook.app.handle_event", fake_handle)
    return received


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _post(client: TestClient, payload: dict[str, Any], *, secret: str = _SECRET) -> Any:
    """用指定 secret 簽章後送出。body 必須與簽章的 bytes 完全一致。"""
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    return client.post(
        "/callback",
        content=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
    )


class TestHealth:
    def test_健康檢查回_ok(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestSignatureGate:
    def test_正確簽章放行(self, client: TestClient, handled: list[dict]) -> None:
        response = _post(client, {"events": []})

        assert response.status_code == 200

    def test_錯誤簽章回_401(self, client: TestClient, handled: list[dict]) -> None:
        response = _post(client, {"events": [{"type": "follow"}]}, secret="wrong")

        assert response.status_code == 401
        assert handled == []

    def test_沒有簽章_header_回_401(self, client: TestClient, handled: list[dict]) -> None:
        response = client.post("/callback", json={"events": []})

        assert response.status_code == 401
        assert handled == []


class TestEventDispatch:
    def test_每個事件都會被分派(self, client: TestClient, handled: list[dict]) -> None:
        """LINE 一次可送多個事件，漏掉任何一個都是資料遺失。"""
        events = [{"type": "follow"}, {"type": "message"}, {"type": "unfollow"}]

        _post(client, {"events": events})

        assert handled == events

    def test_沒有_events_欄位也不會壞(self, client: TestClient, handled: list[dict]) -> None:
        """LINE 的驗證請求就是空 body。"""
        response = _post(client, {})

        assert response.status_code == 200
        assert handled == []


class TestFailureIsolation:
    def test_單一事件失敗仍回_200(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """回 500 會讓 LINE 重送整批事件，造成重複處理。"""

        async def always_fail(_event: dict) -> None:
            raise RuntimeError("處理爆炸")

        monkeypatch.setattr("src.webhook.app.handle_event", always_fail)

        response = _post(client, {"events": [{"type": "follow"}]})

        assert response.status_code == 200

    def test_一個事件失敗不影響同批其他事件(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received: list[dict] = []

        async def fail_first(event: dict) -> None:
            if event["type"] == "follow":
                raise RuntimeError("處理爆炸")
            received.append(event)

        monkeypatch.setattr("src.webhook.app.handle_event", fail_first)

        _post(client, {"events": [{"type": "follow"}, {"type": "message"}]})

        assert received == [{"type": "message"}]
