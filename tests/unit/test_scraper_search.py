"""搜尋結果解析測試。

fixture 取自 2026-08-01 對
https://www.104.com.tw/jobs/search/api/jobs?area=6001001000&jobcat=2007001016
的真實回應（裁剪掉 tags/labels/interactionRecord 等與解析無關的欄位）。
"""

import pytest

from src.scraper.client import (
    ScraperError,
    _extract_job_list,
    _extract_job_slug,
    _format_salary,
    _normalize_url,
    _search_item_to_job,
)


@pytest.fixture
def search_item() -> dict:
    """真實搜尋結果的一筆職缺。"""
    return {
        "appearDate": "20260801",
        "custName": "昇智科技有限公司",
        "description": "1. 具備Java 實務開發、測試與系統維護經驗。",
        "jobAddrNoDesc": "台北市松山區",
        "jobName": "Java工程師",
        "jobNo": "14950369",
        "link": {
            "job": "https://www.104.com.tw/job/8wfs1",
            "cust": "https://www.104.com.tw/company/1a2x6bnfkp",
        },
        "salaryHigh": 0,
        "salaryLow": 0,
    }


class TestExtractJobList:
    def test_data_直接就是陣列(self, search_item: dict) -> None:
        payload = {"data": [search_item], "metadata": {}}
        assert _extract_job_list(payload) == [search_item]

    def test_空結果不算錯誤(self) -> None:
        """搜尋不到職缺是正常情況，不該當成抓取失敗。"""
        assert _extract_job_list({"data": []}) == []

    @pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {"list": []}}])
    def test_data_不是陣列要報錯(self, payload: dict) -> None:
        """特別涵蓋 data.list —— 舊教學的錯誤結構，改版時要能立刻辨識。"""
        with pytest.raises(ScraperError, match="不是陣列"):
            _extract_job_list(payload)


class TestSearchItemToJob:
    def test_基本欄位對應正確(self, search_item: dict) -> None:
        job = _search_item_to_job(search_item)

        assert job.title == "Java工程師"
        assert job.company == "昇智科技有限公司"
        assert job.location == "台北市松山區"
        assert job.url == "https://www.104.com.tw/job/8wfs1"

    def test_job_id_用短碼而非_jobNo(self, search_item: dict) -> None:
        """詳情 API 只吃短碼，用 jobNo 會查不到。"""
        job = _search_item_to_job(search_item)

        assert job.job_id == "8wfs1"
        assert job.job_id != search_item["jobNo"]

    def test_條件與福利留空待詳情補齊(self, search_item: dict) -> None:
        job = _search_item_to_job(search_item)

        assert job.requirement == ""
        assert job.benefit == ""

    def test_缺少_link_也不炸(self, search_item: dict) -> None:
        del search_item["link"]
        job = _search_item_to_job(search_item)

        assert job.job_id == ""
        assert job.title == "Java工程師"


class TestExtractJobSlug:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.104.com.tw/job/8wfs1", "8wfs1"),
            ("https://www.104.com.tw/job/8wfs1/", "8wfs1"),
            ("//www.104.com.tw/job/94c2f", "94c2f"),
            ("", ""),
        ],
    )
    def test_取出網址最後一段(self, url: str, expected: str) -> None:
        assert _extract_job_slug(url) == expected


class TestFormatSalary:
    def test_上下限都是零代表面議(self) -> None:
        assert _format_salary({"salaryLow": 0, "salaryHigh": 0}) == "待遇面議"

    def test_一般區間(self) -> None:
        assert _format_salary({"salaryLow": 50000, "salaryHigh": 75000}) == "50,000 ~ 75,000"

    def test_哨兵值視為以上(self) -> None:
        """104 用 9999999 表示沒有上限，直接印出來會很怪。"""
        assert _format_salary({"salaryLow": 50000, "salaryHigh": 9999999}) == "50,000 以上"

    def test_上下限相同只印一個(self) -> None:
        assert _format_salary({"salaryLow": 29500, "salaryHigh": 29500}) == "29,500"

    def test_只有上限(self) -> None:
        assert _format_salary({"salaryLow": 0, "salaryHigh": 60000}) == "60,000 以下"

    def test_欄位缺失視為面議(self) -> None:
        assert _format_salary({}) == "待遇面議"


class TestNormalizeUrl:
    def test_補上_https_scheme(self) -> None:
        """104 回的是 protocol-relative 連結，直接丟給使用者點不開。"""
        assert _normalize_url("//www.104.com.tw/job/aaa11") == "https://www.104.com.tw/job/aaa11"

    def test_已完整的網址原樣保留(self) -> None:
        url = "https://www.104.com.tw/job/aaa11"
        assert _normalize_url(url) == url
