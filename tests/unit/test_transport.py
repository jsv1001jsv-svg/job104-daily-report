"""傳輸層測試。

用假的 AsyncSession 取代 curl_cffi —— 這裡要驗的是「回應怎麼被判讀、
失敗怎麼包裝」，不是 curl_cffi 本身。真實連線的驗證屬於整合測試，
見 scripts/probe_104_api.py。

其中 `test_一定要帶上瀏覽器指紋` 是刻意的迴歸測試：impersonate 設定
是整個模組唯一不能拿掉的東西，拿掉就會退回 Python 原生 TLS 指紋、
被 Cloudflare 全數擋下（見 CLAUDE.md 第 5.1 節）。
"""

from typing import Any

import pytest
from curl_cffi.requests.exceptions import RequestException

from src.scraper.transport import HttpSession, TransportError


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, raises: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": []}
        self._raises = raises

    def json(self) -> Any:
        if self._raises:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class FakeSession:
    """記錄請求內容的假 curl_cffi session。"""

    created_with: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        FakeSession.created_with = kwargs
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.response = FakeResponse()
        self.error: Exception | None = None

    async def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "headers": headers or {}})
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_curl(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.created_with = {}
    monkeypatch.setattr("src.scraper.transport.AsyncSession", FakeSession)


def _inner(session: HttpSession) -> FakeSession:
    """取出被包住的假 session，用來斷言或設定回應。"""
    return session._session  # type: ignore[return-value]


class TestSessionLifecycle:
    async def test_一定要帶上瀏覽器指紋(self) -> None:
        """impersonate 是通過 Cloudflare 的唯一關鍵，拿掉就全數 403。"""
        async with HttpSession():
            pass

        assert FakeSession.created_with["impersonate"] == "chrome"

    async def test_離開_context_會關閉連線(self) -> None:
        async with HttpSession() as session:
            inner = _inner(session)

        assert inner.closed is True

    async def test_尚未啟動就使用會明確報錯(self) -> None:
        """比讓 None 冒出 AttributeError 好debug。"""
        session = HttpSession()

        with pytest.raises(TransportError, match="尚未啟動"):
            await session.fetch_search({"keyword": "k"})

    async def test_重複關閉不會出錯(self) -> None:
        session = HttpSession()
        await session.start()
        await session.close()

        await session.close()  # 不應拋例外


class TestFetchSearch:
    async def test_打搜尋_API_並帶上搜尋頁_Referer(self) -> None:
        """104 會擋掉沒有 Referer 的請求。"""
        async with HttpSession() as session:
            await session.fetch_search({"keyword": "後端工程師"})
            call = _inner(session).calls[0]

        assert call["url"] == "https://www.104.com.tw/jobs/search/api/jobs"
        assert call["params"] == {"keyword": "後端工程師"}
        assert call["headers"]["Referer"] == "https://www.104.com.tw/jobs/search/"

    async def test_回傳原始_JSON_不做解析(self) -> None:
        """解析是 client.py 的職責，這層原樣傳遞。"""
        payload = {"data": [{"jobName": "工程師"}], "metadata": {}}

        async with HttpSession() as session:
            _inner(session).response = FakeResponse(payload=payload)
            result = await session.fetch_search({"keyword": "k"})

        assert result == payload


class TestFetchJson:
    async def test_Referer_由呼叫端指定(self) -> None:
        """詳情 API 的 Referer 須指向該職缺頁，指向搜尋頁會被擋。"""
        async with HttpSession() as session:
            await session.fetch_json(
                "https://www.104.com.tw/api/jobs/aaa11",
                referer="https://www.104.com.tw/job/aaa11",
            )
            call = _inner(session).calls[0]

        assert call["url"] == "https://www.104.com.tw/api/jobs/aaa11"
        assert call["headers"]["Referer"] == "https://www.104.com.tw/job/aaa11"
        assert call["params"] is None


class TestErrorHandling:
    async def test_403_的訊息要指向_TLS_指紋(self) -> None:
        """日後若 104 收緊指紋要求，錯誤訊息要直接指出該查什麼。"""
        async with HttpSession() as session:
            _inner(session).response = FakeResponse(status_code=403)

            with pytest.raises(TransportError, match="TLS 指紋"):
                await session.fetch_search({"keyword": "k"})

    async def test_其他非_200_也轉成_TransportError(self) -> None:
        async with HttpSession() as session:
            _inner(session).response = FakeResponse(status_code=500)

            with pytest.raises(TransportError, match="HTTP 500"):
                await session.fetch_search({"keyword": "k"})

    async def test_回應不是_JSON_轉成_TransportError(self) -> None:
        async with HttpSession() as session:
            _inner(session).response = FakeResponse(raises=True)

            with pytest.raises(TransportError, match="不是合法 JSON"):
                await session.fetch_search({"keyword": "k"})

    async def test_網路錯誤轉成_TransportError(self) -> None:
        """呼叫端只需認識 TransportError，不必知道底層用什麼 HTTP 函式庫。"""
        async with HttpSession() as session:
            _inner(session).error = RequestException("連線逾時")

            with pytest.raises(TransportError, match="請求失敗"):
                await session.fetch_search({"keyword": "k"})
