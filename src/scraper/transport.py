"""以模仿瀏覽器 TLS 指紋的 HTTP client 取得 104 資料，通過 Cloudflare 防護。

為什麼不是普通的 httpx，也不是 Playwright，完整脈絡見 CLAUDE.md 第 5.1 節。
一句話版本：

    Cloudflare 是在 **TLS handshake** 那一層辨識機器人的（JA3/JA4 指紋），
    不是看 header。所以補再多 header 都沒用，但只要 handshake 長得像 Chrome
    就直接放行 —— 不需要真的開一個瀏覽器。

本模組不解析任何欄位，只負責把 JSON 拿到手；解析仍歸 client.py。

兩個方法對應兩支 API：
  - `fetch_search()`：搜尋 API，回職缺清單（沒有條件與福利）。
  - `fetch_json()`：任意 GET，詳情 API 用這個補齊條件與福利。
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.104.com.tw/jobs/search/api/jobs"
_SEARCH_REFERER = "https://www.104.com.tw/jobs/search/"

_TIMEOUT_SECONDS = 30

# ⚠️ 這是整個模組的關鍵設定，不要拿掉。
# curl_cffi 會照這個目標重現對應瀏覽器的 TLS 與 HTTP/2 指紋；
# 拿掉就退回 Python 原生指紋，104 會回 403「Just a moment...」。
# 用不帶版本號的 "chrome" 讓 curl_cffi 自己選它支援的最新版，
# 免得 104 哪天開始要求較新的指紋時我們還鎖在舊版本。
_IMPERSONATE = "chrome"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


class TransportError(RuntimeError):
    """取得 104 資料失敗。呼叫端應記錄並跳過，不要讓整批日報中斷。"""


class HttpSession:
    """一個共用的 HTTP session。

    整批抓取共用同一個 session：連線可以重用，也比每筆各開一條連線
    更接近正常瀏覽行為。

    用法：

        async with HttpSession() as session:
            payload = await session.fetch_search(params)
            detail = await session.fetch_json(url, referer=page_url)
    """

    def __init__(self) -> None:
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> HttpSession:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        self._session = AsyncSession(
            headers=_HEADERS,
            timeout=_TIMEOUT_SECONDS,
            impersonate=_IMPERSONATE,
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_search(self, params: dict[str, str]) -> dict[str, Any]:
        """打搜尋 API。

        Args:
            params: 搜尋條件，見 client.build_search_params()。

        Returns:
            搜尋 API 的原始 JSON。

        Raises:
            TransportError: 網路錯誤、非 200，或回應不是合法 JSON。
        """
        return await self._get(_SEARCH_URL, params=params, referer=_SEARCH_REFERER)

    async def fetch_json(self, url: str, *, referer: str) -> dict[str, Any]:
        """打任意 GET API。

        Args:
            url: 目標 API 網址。
            referer: 104 會檢查 Referer，詳情 API 需指向該職缺頁本身。

        Returns:
            回應的 JSON。

        Raises:
            TransportError: 網路錯誤、非 200，或回應不是合法 JSON。
        """
        return await self._get(url, params=None, referer=referer)

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, str] | None,
        referer: str,
    ) -> dict[str, Any]:
        session = self._require_session()

        try:
            response = await session.get(url, params=params, headers={"Referer": referer})
        except RequestException as exc:
            raise TransportError(f"請求失敗（url={url}）：{exc}") from exc

        if response.status_code != 200:
            # 403 幾乎都代表 TLS 指紋不再被接受（例如 curl_cffi 版本過舊），
            # 訊息要講清楚，否則日後只看到 403 會誤以為是參數組錯。
            raise TransportError(
                f"104 回應 HTTP {response.status_code}（url={url}）。"
                "若為 403，多半是 Cloudflare 不再接受目前的 TLS 指紋，"
                "請確認 curl_cffi 版本與 _IMPERSONATE 設定。"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise TransportError(f"104 回應不是合法 JSON（url={url}）：{exc}") from exc

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise TransportError("HttpSession 尚未啟動，請先 await start() 或使用 async with")
        return self._session
