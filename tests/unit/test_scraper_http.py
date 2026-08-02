"""client.py 的 HTTP 層測試（純 httpx 路徑）。

⚠️ 這條路徑對正式的 104 已知不可用 —— www.104.com.tw 有 Cloudflare 防護，
純 httpx 一律 403（見 CLAUDE.md 第 5.1 節），正式流程走 fetcher.py + 瀏覽器。

那為什麼還要測？因為錯誤處理契約（什麼情況拋 ScraperError、
client 歸誰關）在兩條路徑上是共用的，而且這個模組還沒被刪除。
解析邏輯本身的測試在 test_scraper_search.py / test_scraper_detail.py。
"""

from typing import Any

import httpx
import pytest
import respx

from src.scraper.client import (
    _SEARCH_URL,
    ScraperError,
    _normalize_url,
    fetch_job_detail,
    job_detail_url,
    search_jobs,
)


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """關掉禮貌性延遲，測試不該真的等秒數。"""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("src.scraper.client.asyncio.sleep", instant)


def _search_payload(*slugs: str) -> dict[str, Any]:
    return {
        "data": [
            {
                "jobName": f"職缺 {slug}",
                "custName": "某公司",
                "jobAddrNoDesc": "台北市信義區",
                "description": "工作內容",
                "salaryLow": 0,
                "salaryHigh": 0,
                "link": {"job": f"//www.104.com.tw/job/{slug}"},
            }
            for slug in slugs
        ],
        "metadata": {},
    }


def _detail_payload() -> dict[str, Any]:
    return {
        "data": {
            "header": {"jobName": "Python 工程師", "custName": "某公司"},
            "jobDetail": {"jobDescription": "寫程式", "salary": "月薪 60,000 元"},
            "condition": {"workExp": "3年以上", "edu": "大學"},
            "welfare": {"tag": ["年終"], "welfare": "彈性工時"},
        }
    }


class TestSearchJobs:
    @respx.mock
    async def test_成功回傳解析後的職缺(self) -> None:
        respx.get(_SEARCH_URL).mock(
            return_value=httpx.Response(200, json=_search_payload("aaa11", "bbb22"))
        )

        jobs = await search_jobs("後端工程師", "6001001000", limit=10)

        assert len(jobs) == 2
        assert jobs[0].job_id == "aaa11"

    @respx.mock
    async def test_HTTP_錯誤轉成_ScraperError(self) -> None:
        """Cloudflare 擋下時就是這個路徑（403）。"""
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(403, text="Just a moment..."))

        with pytest.raises(ScraperError, match="搜尋請求失敗"):
            await search_jobs("後端工程師", "6001001000", limit=10)

    @respx.mock
    async def test_網路錯誤轉成_ScraperError(self) -> None:
        respx.get(_SEARCH_URL).mock(side_effect=httpx.ConnectTimeout("逾時"))

        with pytest.raises(ScraperError, match="搜尋請求失敗"):
            await search_jobs("後端工程師", "6001001000", limit=10)

    @respx.mock
    async def test_回應不是_JSON_轉成_ScraperError(self) -> None:
        """403 挑戰頁回的是 HTML，訊息要說得出是格式問題而非網路問題。"""
        respx.get(_SEARCH_URL).mock(
            return_value=httpx.Response(200, text="<html>Just a moment...</html>")
        )

        with pytest.raises(ScraperError, match="不是合法 JSON"):
            await search_jobs("後端工程師", "6001001000", limit=10)

    @respx.mock
    async def test_注入的_client_不會被關掉(self) -> None:
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=_search_payload("a1")))

        async with httpx.AsyncClient() as client:
            await search_jobs("k", "a", limit=5, client=client)
            assert client.is_closed is False


class TestFetchJobDetail:
    @respx.mock
    async def test_成功組出完整職缺(self) -> None:
        respx.get(job_detail_url("aaa11")).mock(
            return_value=httpx.Response(200, json=_detail_payload())
        )

        async with httpx.AsyncClient() as client:
            job = await fetch_job_detail("aaa11", client)

        assert job.title == "Python 工程師"
        assert "3年以上" in job.requirement
        assert "年終" in job.benefit

    @respx.mock
    async def test_Referer_指向該職缺頁(self) -> None:
        """指向搜尋頁會被 104 擋掉。"""
        route = respx.get(job_detail_url("aaa11")).mock(
            return_value=httpx.Response(200, json=_detail_payload())
        )

        async with httpx.AsyncClient() as client:
            await fetch_job_detail("aaa11", client)

        assert route.calls.last.request.headers["Referer"].endswith("/job/aaa11")

    @respx.mock
    async def test_HTTP_錯誤轉成_ScraperError(self) -> None:
        respx.get(job_detail_url("aaa11")).mock(return_value=httpx.Response(404))

        async with httpx.AsyncClient() as client:
            with pytest.raises(ScraperError, match="詳情請求失敗"):
                await fetch_job_detail("aaa11", client)

    @respx.mock
    async def test_回應不是_JSON_轉成_ScraperError(self) -> None:
        respx.get(job_detail_url("aaa11")).mock(return_value=httpx.Response(200, text="not json"))

        async with httpx.AsyncClient() as client:
            with pytest.raises(ScraperError, match="不是合法 JSON"):
                await fetch_job_detail("aaa11", client)


class TestNormalizeUrl:
    def test_補上_https_scheme(self) -> None:
        """104 回的是 protocol-relative 連結，直接丟給使用者點不開。"""
        assert _normalize_url("//www.104.com.tw/job/aaa11") == "https://www.104.com.tw/job/aaa11"

    def test_已完整的網址原樣保留(self) -> None:
        url = "https://www.104.com.tw/job/aaa11"
        assert _normalize_url(url) == url
