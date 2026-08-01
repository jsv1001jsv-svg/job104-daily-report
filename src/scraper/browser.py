"""以真實瀏覽器取得 104 資料，繞過 Cloudflare bot 防護。

為什麼需要這一層，以及為什麼選「攔截 XHR」而非解析 DOM，
完整理由見 CLAUDE.md 第 5.1 節。一句話版本：

    瀏覽器只負責「通過 Cloudflare 驗證」，資料解析仍走 client.py
    已驗證的那條路 —— 本模組不解析任何欄位，只負責把 JSON 拿到手。

兩種取得方式，用途不同：
  - `fetch_search()`：導頁到搜尋頁，攔截頁面自己發出的搜尋 API 回應。
      導頁同時也是取得 Cloudflare 通行證（cf_clearance）的手段。
  - `fetch_json()`：在**已通過驗證的頁面內**執行 fetch，直接取任意 API。
      走的是 Chromium 自己的網路堆疊，因此 cookie 與 TLS 指紋都是真的。
      詳情 API 用這個，20 筆職缺只需 1 次導頁而非 20 次，快一個量級。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from types import TracebackType
from typing import Any
from urllib.parse import urlencode

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

_SEARCH_PAGE_URL = "https://www.104.com.tw/jobs/search/"
_SEARCH_API_PATH = "/jobs/search/api/jobs"

# Cloudflare 的挑戰頁需要時間跑完，實測約 10 秒。首次導頁會先收到 403，
# 挑戰通過後頁面才會自行載入並發出 API 請求 —— 逾時要涵蓋這整段。
_NAVIGATION_TIMEOUT_MS = 60_000
_API_TIMEOUT_MS = 60_000

_LOCALE = "zh-TW"
_TIMEZONE = "Asia/Taipei"
_VIEWPORT = {"width": 1440, "height": 900}

# ⚠️ 這個 UA 是通過 Cloudflare 的必要條件，不要拿掉。
# headless 模式的預設 UA 含 "HeadlessChrome" 字樣，等於自報是自動化瀏覽器，
# 實測會卡在挑戰頁永遠過不去；改成一般 Chrome 的 UA 後約 10 秒即通過。
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_ACCEPT_LANGUAGE = "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"


class BrowserError(RuntimeError):
    """瀏覽器操作失敗。呼叫端應記錄並跳過，不要讓整批日報中斷。"""


class BrowserSession:
    """一個 Chromium 實例與其瀏覽器上下文。

    Cloudflare 的通行證綁在 context 的 cookie 上，所以**同一批抓取要共用
    同一個 session**：開一次瀏覽器、驗證一次，之後所有請求都沿用。
    每位使用者各開一次瀏覽器會慢好幾倍，也更容易被判定為異常流量。

    用法：

        async with BrowserSession() as session:
            payload = await session.fetch_search(params)
            detail = await session.fetch_json(url, referer=page_url)
    """

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> BrowserSession:
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
        """啟動瀏覽器。失敗時保證不留下半開的資源。"""
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                # channel="chromium" 用完整的 Chromium 而非精簡的 headless shell，
                # 後者缺少一些瀏覽器 API，特徵較明顯
                channel="chromium",
                args=[
                    # 拿掉 navigator.webdriver 這類最明顯的自動化特徵。
                    # 不是為了偽裝成別的東西 —— 它本來就是真的瀏覽器，
                    # 只是預設會主動聲明「我被程式控制」，那會被直接擋下。
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            self._context = await self._browser.new_context(
                locale=_LOCALE,
                timezone_id=_TIMEZONE,
                viewport=_VIEWPORT,
                user_agent=_USER_AGENT,
                extra_http_headers={"Accept-Language": _ACCEPT_LANGUAGE},
            )
            self._context.set_default_timeout(_API_TIMEOUT_MS)
            self._page = await self._context.new_page()
        except PlaywrightError as exc:
            await self.close()
            raise BrowserError(f"瀏覽器啟動失敗：{exc}") from exc

    async def close(self) -> None:
        """關閉所有資源。任一步失敗不影響其餘步驟 —— 半開的瀏覽器會殘留行程。"""
        if self._context is not None:
            await self._safely(self._context.close(), "context")
        if self._browser is not None:
            await self._safely(self._browser.close(), "browser")
        if self._playwright is not None:
            await self._safely(self._playwright.stop(), "playwright")

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def fetch_search(self, params: dict[str, str]) -> dict[str, Any]:
        """導頁到搜尋頁，攔截頁面自己發出的搜尋 API 回應。

        搜尋頁的網址參數與 API 的參數同名，所以把 params 掛在頁面網址上，
        頁面就會用這組條件去打 API —— 我們不必自己組 API 請求。

        Args:
            params: 搜尋條件，見 client.build_search_params()。

        Returns:
            搜尋 API 的原始 JSON。

        Raises:
            BrowserError: 導頁逾時、Cloudflare 未放行，或沒攔到 API 回應。
        """
        page = self._require_page()
        url = f"{_SEARCH_PAGE_URL}?{_encode(params)}"

        try:
            # 必須等「成功」的那一次：頁面會先打一次拿到 403，觸發 Cloudflare
            # 挑戰，通過後才重打一次成功的。只比對網址會攔到失敗的那次。
            async with page.expect_response(
                lambda response: _SEARCH_API_PATH in response.url and response.ok,
                timeout=_API_TIMEOUT_MS,
            ) as intercepted:
                await page.goto(url, timeout=_NAVIGATION_TIMEOUT_MS, wait_until="commit")
            response = await intercepted.value
        except PlaywrightTimeoutError as exc:
            raise BrowserError(
                f"未攔截到搜尋 API 回應（params={params}）。"
                "可能是 Cloudflare 未放行，或 104 改了 API 路徑。"
            ) from exc
        except PlaywrightError as exc:
            raise BrowserError(f"搜尋導頁失敗（params={params}）：{exc}") from exc

        if not response.ok:
            raise BrowserError(f"搜尋 API 回應 HTTP {response.status}（params={params}）")

        try:
            return await response.json()
        except (PlaywrightError, ValueError) as exc:
            raise BrowserError(f"搜尋 API 回應不是合法 JSON（params={params}）：{exc}") from exc

    async def fetch_json(self, url: str, *, referer: str) -> dict[str, Any]:
        """在已通過驗證的頁面內 fetch 任意 API。

        用 page.evaluate 而非 Playwright 的 APIRequestContext —— 後者走自己的
        HTTP 堆疊，TLS 指紋與 Chromium 不同，有被 Cloudflare 擋下的風險。
        在頁面內執行則與一般網頁請求無異。

        Args:
            url: 目標 API 網址。
            referer: 104 會檢查 Referer，詳情 API 需指向該職缺頁本身。

        Returns:
            回應的 JSON。

        Raises:
            BrowserError: 請求失敗、非 200，或回應不是 JSON。
        """
        page = self._require_page()

        try:
            result = await page.evaluate(_FETCH_JSON_SCRIPT, {"url": url, "referer": referer})
        except PlaywrightError as exc:
            raise BrowserError(f"頁面內請求失敗（url={url}）：{exc}") from exc

        if error := result.get("error"):
            raise BrowserError(f"頁面內請求失敗（url={url}）：{error}")
        if (status := result.get("status")) != 200:
            raise BrowserError(f"API 回應 HTTP {status}（url={url}）")

        return result["body"]

    def _require_page(self) -> Page:
        if self._page is None:
            raise BrowserError("BrowserSession 尚未啟動，請先 await start() 或使用 async with")
        return self._page

    @staticmethod
    async def _safely(coro: Awaitable[None], name: str) -> None:
        """關閉單一資源，失敗只記錄不拋出。"""
        try:
            await coro
        except PlaywrightError:
            logger.warning("關閉 %s 時發生錯誤，已忽略", name, exc_info=True)


# 在頁面內執行的 fetch。回傳統一結構，讓 Python 端不必分辨例外種類。
# 刻意不 throw —— Playwright 傳回的 JS 例外訊息很難讀，自己包裝比較清楚。
_FETCH_JSON_SCRIPT = """
async ({ url, referer }) => {
  try {
    const response = await fetch(url, {
      headers: { 'Accept': 'application/json, text/plain, */*', 'Referer': referer },
      credentials: 'include',
    });
    if (!response.ok) {
      return { status: response.status, body: null, error: null };
    }
    return { status: response.status, body: await response.json(), error: null };
  } catch (e) {
    return { status: 0, body: null, error: String(e) };
  }
}
"""


def _encode(params: dict[str, str]) -> str:
    """組查詢字串。用 urlencode 而非自行串接，避免中文關鍵字沒被編碼。"""
    return urlencode(params)
