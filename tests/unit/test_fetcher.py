"""抓取流程測試。

用假的 session 取代真實 HTTP —— 這裡要驗的是「流程如何處理成功與失敗」，
不是傳輸層本身（那在 test_transport.py）。真實連線的驗證屬於整合測試，
見 scripts/probe_104_api.py。
"""

from typing import Any

import pytest

from src.scraper.client import ScraperError
from src.scraper.fetcher import fetch_jobs
from src.scraper.transport import TransportError


class FakeSession:
    """可控制回應與失敗的假 HttpSession。"""

    def __init__(
        self,
        search_payload: dict[str, Any] | None = None,
        detail_payloads: dict[str, Any] | None = None,
        search_error: Exception | None = None,
    ) -> None:
        self._search_payload = search_payload or {"data": []}
        self._detail_payloads = detail_payloads or {}
        self._search_error = search_error
        self.searched_params: dict[str, str] | None = None
        self.detail_calls: list[tuple[str, str]] = []

    async def fetch_search(self, params: dict[str, str]) -> dict[str, Any]:
        if self._search_error:
            raise self._search_error
        self.searched_params = params
        return self._search_payload

    async def fetch_json(self, url: str, *, referer: str) -> dict[str, Any]:
        self.detail_calls.append((url, referer))
        job_id = url.rsplit("/", 1)[-1]
        payload = self._detail_payloads.get(job_id)
        if payload is None:
            raise TransportError(f"假的失敗：{job_id}")
        return payload


def make_search_payload(*slugs: str) -> dict[str, Any]:
    return {
        "data": [
            {
                "jobName": f"職缺 {slug}",
                "custName": "某公司",
                "jobAddrNoDesc": "台北市松山區",
                "description": "工作內容",
                "salaryLow": 0,
                "salaryHigh": 0,
                "link": {"job": f"https://www.104.com.tw/job/{slug}"},
            }
            for slug in slugs
        ],
        "metadata": {},
    }


def make_detail_payload(title: str) -> dict[str, Any]:
    return {
        "data": {
            "header": {"jobName": title, "custName": "某公司"},
            "jobDetail": {"jobDescription": "詳細工作內容", "salary": "待遇面議"},
            "condition": {"workExp": "3年以上", "edu": "大學"},
            "welfare": {"tag": ["零食櫃"], "welfare": "三節獎金"},
        }
    }


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """關掉請求間隔，測試不該真的等秒數。"""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("src.scraper.fetcher.asyncio.sleep", instant)


class TestFetchJobs:
    async def test_搜尋結果會補上條件與福利(self) -> None:
        session = FakeSession(
            search_payload=make_search_payload("aaa11"),
            detail_payloads={"aaa11": make_detail_payload("Java 工程師")},
        )

        jobs = await fetch_jobs(session, keyword="後端工程師", area_code="6001001000", limit=5)

        assert len(jobs) == 1
        assert "3年以上" in jobs[0].requirement
        assert "零食櫃" in jobs[0].benefit

    async def test_詳情的_Referer_指向該職缺頁(self) -> None:
        """指向搜尋頁會被 104 擋掉。"""
        session = FakeSession(
            search_payload=make_search_payload("aaa11"),
            detail_payloads={"aaa11": make_detail_payload("Java 工程師")},
        )

        await fetch_jobs(session, keyword="k", area_code="a", limit=5)

        url, referer = session.detail_calls[0]
        assert url == "https://www.104.com.tw/api/jobs/aaa11"
        assert referer == "https://www.104.com.tw/job/aaa11"

    async def test_單筆詳情失敗仍保留該職缺(self) -> None:
        """少兩個欄位的職缺，仍比整批漏掉這筆對使用者有用。"""
        session = FakeSession(
            search_payload=make_search_payload("aaa11", "bbb22"),
            detail_payloads={"aaa11": make_detail_payload("有詳情的")},
        )

        jobs = await fetch_jobs(session, keyword="k", area_code="a", limit=5)

        assert len(jobs) == 2
        assert jobs[1].requirement == ""      # 失敗那筆退回搜尋結果
        assert jobs[1].title == "職缺 bbb22"  # 但基本欄位還在

    async def test_搜尋失敗要往上拋(self) -> None:
        """沒有搜尋結果就沒有日報可做，與單筆詳情失敗不同。"""
        session = FakeSession(search_error=TransportError("Cloudflare 擋下"))

        with pytest.raises(ScraperError, match="搜尋失敗"):
            await fetch_jobs(session, keyword="k", area_code="a", limit=5)

    async def test_搜尋無結果回傳空清單且不打詳情(self) -> None:
        session = FakeSession(search_payload={"data": []})

        jobs = await fetch_jobs(session, keyword="k", area_code="a", limit=5)

        assert jobs == []
        assert session.detail_calls == []

    async def test_limit_會限制筆數(self) -> None:
        session = FakeSession(search_payload=make_search_payload("a1", "b2", "c3"))

        jobs = await fetch_jobs(session, keyword="k", area_code="a", limit=2)

        assert len(jobs) == 2

    async def test_有給_jobcat_就不送_keyword(self) -> None:
        """兩者並存時 104 的行為未驗證，明確二選一避免不確定性。"""
        session = FakeSession()

        await fetch_jobs(
            session, keyword="後端工程師", area_code="6001001000", limit=5, jobcat="2007001016"
        )

        assert session.searched_params is not None
        assert session.searched_params["jobcat"] == "2007001016"
        assert "keyword" not in session.searched_params
