"""OpenRouter 摘要測試。不打真實 API。

這個模組的核心承諾是「摘要失敗不能讓日報失敗」，
所以降級路徑的測試比成功路徑更重要 —— 成功路徑只有一種，
失敗路徑有網路錯誤、非 JSON、欄位缺失等好幾種。
"""

from dataclasses import replace

import httpx
import respx

from src.models import Job
from src.summarizer.openrouter import _API_URL, summarize


def _completion(content: str) -> httpx.Response:
    """組出 OpenRouter chat completions 的回應外殼。"""
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


_GOOD_JSON = '{"description": "寫 API", "requirement": "會 Python", "benefit": "有零食"}'


class TestSummarizeSuccess:
    @respx.mock
    async def test_解析模型回應填入摘要欄位(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=_completion(_GOOD_JSON))

        result = await summarize(sample_job)

        assert result.summary_description == "寫 API"
        assert result.summary_requirement == "會 Python"
        assert result.summary_benefit == "有零食"
        assert result.is_summarized is True

    @respx.mock
    async def test_模型用_code_fence_包住仍能解析(self, sample_job: Job) -> None:
        """實測不少便宜模型會擅自加上 ```json 標記。"""
        respx.post(_API_URL).mock(return_value=_completion(f"```json\n{_GOOD_JSON}\n```"))

        result = await summarize(sample_job)

        assert result.summary_description == "寫 API"

    @respx.mock
    async def test_原始_job_不被修改(self, sample_job: Job) -> None:
        """frozen dataclass 的保證，但這是跨模組資料流的關鍵前提，值得明測。"""
        respx.post(_API_URL).mock(return_value=_completion(_GOOD_JSON))

        result = await summarize(sample_job)

        assert sample_job.summary_description is None
        assert result is not sample_job

    @respx.mock
    async def test_送出的請求帶金鑰與模型名(self, sample_job: Job) -> None:
        route = respx.post(_API_URL).mock(return_value=_completion(_GOOD_JSON))

        await summarize(sample_job)

        request = route.calls.last.request
        assert request.headers["Authorization"].startswith("Bearer ")
        assert b"messages" in request.content


class TestSummarizeFallback:
    """任何失敗都要降級成截斷原文，不能拋例外中斷日報。"""

    @respx.mock
    async def test_HTTP_錯誤降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=httpx.Response(500))

        result = await summarize(sample_job)

        assert result.summary_description == sample_job.description
        assert result.is_summarized is True  # 仍算已處理，不會被下游當成漏網

    @respx.mock
    async def test_網路錯誤降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(side_effect=httpx.ConnectError("連不上"))

        result = await summarize(sample_job)

        assert result.summary_description == sample_job.description

    @respx.mock
    async def test_回應不是_JSON_降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=_completion("我覺得這個職缺不錯"))

        result = await summarize(sample_job)

        assert result.summary_description == sample_job.description

    @respx.mock
    async def test_模型少回一個欄位就整筆降級(self, sample_job: Job) -> None:
        """半套摘要（有工作內容沒條件）比全部退回原文更難debug，寧可整筆降級。"""
        respx.post(_API_URL).mock(return_value=_completion('{"description": "只有這個"}'))

        result = await summarize(sample_job)

        assert result.summary_description == sample_job.description

    @respx.mock
    async def test_回應結構不符降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=httpx.Response(200, json={"choices": []}))

        result = await summarize(sample_job)

        assert result.summary_description == sample_job.description

    @respx.mock
    async def test_過長原文會被截斷並加省略號(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=httpx.Response(500))
        long_job = replace(sample_job, description="字" * 500)

        result = await summarize(long_job)

        assert result.summary_description.endswith("…")
        assert len(result.summary_description) == 121  # 120 字 + 省略號

    @respx.mock
    async def test_福利空白時填未列出(self, sample_job: Job) -> None:
        """日報欄位固定五項，空字串會讓訊息看起來像壞掉。"""
        respx.post(_API_URL).mock(return_value=httpx.Response(500))
        no_benefit = replace(sample_job, benefit="   ")

        result = await summarize(no_benefit)

        assert result.summary_benefit == "未列出"


class TestClientOwnership:
    @respx.mock
    async def test_注入的_client_不會被關掉(self, sample_job: Job) -> None:
        """整批摘要共用一個 client，被單筆關掉的話後續全部失敗。"""
        respx.post(_API_URL).mock(return_value=_completion(_GOOD_JSON))

        async with httpx.AsyncClient() as client:
            await summarize(sample_job, client=client)
            assert client.is_closed is False
