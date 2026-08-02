"""LINE 事件處理測試。不打真實 LINE API，不碰真實 Firestore。

重點在「哪些事件會寫進資料庫」—— webhook 是對外開放的端點，
寫入條件錯了等於讓任何事件都能污染使用者設定。
"""

from typing import Any

import httpx
import pytest
import respx

from src.models import UserConfig
from src.webhook.handlers import _REPLY_URL, handle_event


class RecordingStore:
    """記錄 upsert 呼叫的假 JobStore。"""

    instances: list["RecordingStore"] = []

    def __init__(self) -> None:
        self.saved: list[UserConfig] = []
        RecordingStore.instances.append(self)

    async def upsert_user(self, config: UserConfig) -> None:
        self.saved.append(config)

    @classmethod
    def all_saved(cls) -> list[UserConfig]:
        return [config for instance in cls.instances for config in instance.saved]


@pytest.fixture(autouse=True)
def _fake_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """handlers 內部直接 new JobStore()，從模組層替換掉。"""
    RecordingStore.instances = []
    monkeypatch.setattr("src.webhook.handlers.JobStore", RecordingStore)


@pytest.fixture(autouse=True)
def _mock_line() -> Any:
    """攔下所有回覆請求，預設回 200。"""
    with respx.mock:
        route = respx.post(_REPLY_URL).mock(return_value=httpx.Response(200, json={}))
        yield route


def _replied_text(route: Any) -> str:
    return route.calls.last.request.content.decode("utf-8")


def _follow_event() -> dict:
    return {
        "type": "follow",
        "replyToken": "token123",
        "source": {"userId": "U_new"},
    }


def _text_event(text: str) -> dict:
    return {
        "type": "message",
        "replyToken": "token123",
        "source": {"userId": "U_new"},
        "message": {"type": "text", "text": text},
    }


class TestFollowEvent:
    async def test_加好友回歡迎訊息(self, _mock_line: Any) -> None:
        await handle_event(_follow_event())

        assert _mock_line.called
        assert "歡迎" in _replied_text(_mock_line)

    async def test_加好友不會建立使用者設定(self) -> None:
        """條件是使用者傳訊息才決定的，follow 當下還沒有條件可存。"""
        await handle_event(_follow_event())

        assert RecordingStore.all_saved() == []


class TestTextMessage:
    async def test_有效條件會存進資料庫(self) -> None:
        await handle_event(_text_event("台北 後端工程師"))

        saved = RecordingStore.all_saved()
        assert len(saved) == 1
        assert saved[0].user_id == "U_new"
        assert saved[0].keyword == "後端工程師"
        assert saved[0].raw_query == "台北 後端工程師"
        assert saved[0].area_code

    async def test_設定成功會回確認訊息(self, _mock_line: Any) -> None:
        await handle_event(_text_event("台北 後端工程師"))

        assert "已設定" in _replied_text(_mock_line)

    async def test_無法辨識地區時整句當關鍵字(self) -> None:
        """使用者只打職務名是常見情況，不該當成錯誤。"""
        await handle_event(_text_event("資料工程師"))

        saved = RecordingStore.all_saved()
        assert len(saved) == 1
        assert saved[0].keyword == "資料工程師"

    async def test_只給地區不存檔並回說明(self, _mock_line: Any) -> None:
        await handle_event(_text_event("台北"))

        assert RecordingStore.all_saved() == []
        assert "地區 職務" in _replied_text(_mock_line)

    async def test_空白訊息不存檔(self) -> None:
        await handle_event(_text_event("   "))

        assert RecordingStore.all_saved() == []

    async def test_前後空白會被去除(self) -> None:
        await handle_event(_text_event("  台北 後端工程師  "))

        assert RecordingStore.all_saved()[0].raw_query == "台北 後端工程師"


class TestIgnoredEvents:
    async def test_貼圖訊息被略過(self, _mock_line: Any) -> None:
        """只處理文字。貼圖當條件解析只會存進垃圾資料。"""
        event = {
            "type": "message",
            "replyToken": "t",
            "source": {"userId": "U1"},
            "message": {"type": "sticker", "packageId": "1"},
        }

        await handle_event(event)

        assert RecordingStore.all_saved() == []
        assert not _mock_line.called

    async def test_未支援的事件型別被略過(self, _mock_line: Any) -> None:
        await handle_event({"type": "unfollow", "source": {"userId": "U1"}})

        assert RecordingStore.all_saved() == []
        assert not _mock_line.called


class TestReplyFailure:
    async def test_回覆失敗不影響已存檔的設定(self, _mock_line: Any) -> None:
        """LINE 回覆失敗（例如 token 過期）不該讓使用者的條件也跟著丟掉。"""
        _mock_line.mock(return_value=httpx.Response(400, json={"message": "Invalid reply token"}))

        await handle_event(_text_event("台北 後端工程師"))

        assert len(RecordingStore.all_saved()) == 1
