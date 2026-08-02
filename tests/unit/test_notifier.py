"""LINE 訊息排版與推播測試。不打真實 API。"""

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
import respx

from src.models import DailyReport, Job
from src.notifier.line import (
    _MAX_MESSAGES_PER_PUSH,
    _PUSH_URL,
    NotifierError,
    _build_messages,
    _format_job,
    push_report,
)


def _report(jobs: tuple[Job, ...]) -> DailyReport:
    return DailyReport(
        user_id="U1",
        report_date=datetime(2026, 8, 1, tzinfo=UTC),
        jobs=jobs,
    )


class TestFormatJob:
    def test_包含日報要求的所有欄位(self, sample_job: Job) -> None:
        text = _format_job(1, sample_job)

        assert sample_job.title in text
        assert sample_job.company in text
        assert sample_job.location in text
        assert sample_job.salary in text
        assert sample_job.url in text

    def test_有摘要時優先顯示摘要(self, sample_job: Job) -> None:
        summarized = sample_job.with_summary("摘要內容", "摘要條件", "摘要福利")
        text = _format_job(1, summarized)

        assert "摘要內容" in text
        assert sample_job.description not in text

    def test_無摘要時退回原文(self, sample_job: Job) -> None:
        text = _format_job(1, sample_job)
        assert sample_job.description in text


class TestBuildMessages:
    def test_少量職缺合併成單則訊息(self, sample_job: Job) -> None:
        messages = _build_messages(_report((sample_job, sample_job)))
        assert len(messages) == 1

    def test_標頭顯示職缺筆數(self, sample_job: Job) -> None:
        messages = _build_messages(_report((sample_job,) * 3))
        assert "3 筆新職缺" in messages[0]

    def test_超長內容會自動分成多則(self, sample_job: Job) -> None:
        # 單筆撐大到接近上限，20 筆必然超過單則字數限制
        fat_job = replace(sample_job, description="工作內容說明" * 100)
        messages = _build_messages(_report((fat_job,) * 20))

        assert len(messages) > 1
        assert all(len(m) <= 4800 for m in messages)

    def test_每則訊息都不是空的(self, sample_job: Job) -> None:
        messages = _build_messages(_report((sample_job,) * 20))
        assert all(m.strip() for m in messages)


class TestPushReport:
    @respx.mock
    async def test_推給正確的使用者(self, sample_job: Job) -> None:
        route = respx.post(_PUSH_URL).mock(return_value=httpx.Response(200, json={}))

        await push_report(_report((sample_job,)))

        body = route.calls.last.request.content.decode("utf-8")
        assert '"to": "U1"' in body or '"to":"U1"' in body

    @respx.mock
    async def test_帶上_Bearer_授權(self, sample_job: Job) -> None:
        route = respx.post(_PUSH_URL).mock(return_value=httpx.Response(200, json={}))

        await push_report(_report((sample_job,)))

        assert route.calls.last.request.headers["Authorization"].startswith("Bearer ")

    @respx.mock
    async def test_空日報推無新職缺提示(self) -> None:
        """不推的話使用者無從分辨「今天沒新職缺」與「系統掛了」。"""
        route = respx.post(_PUSH_URL).mock(return_value=httpx.Response(200, json={}))

        await push_report(_report(()))

        assert "今日已無新職缺" in route.calls.last.request.content.decode("utf-8")

    @respx.mock
    async def test_超過單次上限只送前幾則(self, sample_job: Job) -> None:
        """LINE 單次 push 最多 5 則，超過整包會被退。"""
        route = respx.post(_PUSH_URL).mock(return_value=httpx.Response(200, json={}))
        fat_job = replace(sample_job, description="工作內容說明" * 100)

        await push_report(_report((fat_job,) * 60))

        payload = route.calls.last.request.content.decode("utf-8")
        assert payload.count('"type": "text"') <= _MAX_MESSAGES_PER_PUSH

    @respx.mock
    async def test_API_回錯誤轉成_NotifierError(self, sample_job: Job) -> None:
        """呼叫端靠這個例外決定「不要標記已看過」，不能讓 httpx 的例外漏出去。"""
        respx.post(_PUSH_URL).mock(return_value=httpx.Response(429, json={"message": "額度用完"}))

        with pytest.raises(NotifierError, match="U1"):
            await push_report(_report((sample_job,)))

    @respx.mock
    async def test_網路錯誤轉成_NotifierError(self, sample_job: Job) -> None:
        respx.post(_PUSH_URL).mock(side_effect=httpx.ConnectError("連不上"))

        with pytest.raises(NotifierError):
            await push_report(_report((sample_job,)))

    @respx.mock
    async def test_注入的_client_不會被關掉(self, sample_job: Job) -> None:
        respx.post(_PUSH_URL).mock(return_value=httpx.Response(200, json={}))

        async with httpx.AsyncClient() as client:
            await push_report(_report((sample_job,)), client=client)
            assert client.is_closed is False
