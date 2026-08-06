"""LLM 摘要測試。不打真實 API。

這個模組的核心承諾是「摘要失敗不能讓日報失敗」，
所以降級路徑的測試比成功路徑更重要 —— 成功路徑只有一種，
失敗路徑有網路錯誤、非 JSON、欄位缺失等好幾種。

批次是為了額度而非效能：免費層 5-15 RPM，一筆一請求必定被擋。
因此「一批之內單筆壞掉不影響其他筆」也是這裡的重點。

端點取自設定而非寫死，所以這裡先固定一個假位址，
避免測試結果隨本機 .env 指向哪家供應商而變。
"""

import json
from dataclasses import replace

import httpx
import pytest
import respx

from src.config import get_settings
from src.models import Job
from src.summarizer.llm import summarize_jobs

_API_URL = "https://llm.test/v1/chat/completions"


@pytest.fixture(autouse=True)
def _fixed_endpoint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_API_URL", _API_URL)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BATCH_SIZE", "10")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _completion(content: str) -> httpx.Response:
    """組出 OpenAI 相容 chat completions 的回應外殼。"""
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _entries(count: int, label_start: int = 0) -> str:
    """組出模型回應。

    `index` 一律從 0 起算 —— 每一批都是獨立的一次請求，模型只看得到該批的
    編號。`label_start` 只影響內容文字，方便斷言「第 15 筆對到第二批」。
    """
    return json.dumps([
        {
            "index": i,
            "description": f"工作{label_start + i}",
            "requirement": f"條件{label_start + i}",
            "benefit": f"福利{label_start + i}",
        }
        for i in range(count)
    ])


def _jobs(sample_job: Job, count: int) -> list[Job]:
    return [replace(sample_job, job_id=f"j{i}") for i in range(count)]


class TestBatching:
    @respx.mock
    async def test_多筆職缺只發一次請求(self, sample_job: Job) -> None:
        """批次的全部意義：免費額度是以「請求數」計費的。"""
        route = respx.post(_API_URL).mock(return_value=_completion(_entries(10)))

        await summarize_jobs(_jobs(sample_job, 10))

        assert route.call_count == 1

    @respx.mock
    async def test_超過批次大小會分批(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(
            side_effect=[_completion(_entries(10)), _completion(_entries(5, label_start=10))]
        )

        results = await summarize_jobs(_jobs(sample_job, 15))

        assert len(results) == 15
        assert results[14].summary_description == "工作14"

    @respx.mock
    async def test_依序對應回原本的職缺(self, sample_job: Job) -> None:
        """對錯號會讓使用者看到 A 公司的職缺配 B 公司的描述。"""
        respx.post(_API_URL).mock(return_value=_completion(_entries(3)))

        results = await summarize_jobs(_jobs(sample_job, 3))

        assert [j.job_id for j in results] == ["j0", "j1", "j2"]
        assert [j.summary_description for j in results] == ["工作0", "工作1", "工作2"]

    async def test_空清單不發請求(self) -> None:
        """沒有新職缺的日子是常態，不該浪費一次額度。"""
        assert await summarize_jobs([]) == []


class TestPartialFailure:
    @respx.mock
    async def test_模型漏掉某一筆時只有那筆降級(self, sample_job: Job) -> None:
        """整批退回原文太浪費，能救幾筆算幾筆。"""
        partial = json.dumps([
            {"index": 0, "description": "工作0", "requirement": "條件0", "benefit": "福利0"},
            {"index": 2, "description": "工作2", "requirement": "條件2", "benefit": "福利2"},
        ])
        respx.post(_API_URL).mock(return_value=_completion(partial))

        results = await summarize_jobs(_jobs(sample_job, 3))

        assert results[0].summary_description == "工作0"
        assert results[1].summary_description == sample_job.description  # 降級
        assert results[2].summary_description == "工作2"

    @respx.mock
    async def test_某筆少一個欄位就那筆降級(self, sample_job: Job) -> None:
        """半套摘要比整筆退回原文更難 debug。"""
        broken = json.dumps([
            {"index": 0, "description": "只有這個"},
            {"index": 1, "description": "工作1", "requirement": "條件1", "benefit": "福利1"},
        ])
        respx.post(_API_URL).mock(return_value=_completion(broken))

        results = await summarize_jobs(_jobs(sample_job, 2))

        assert results[0].summary_description == sample_job.description
        assert results[1].summary_description == "工作1"

    @respx.mock
    async def test_一批失敗不影響其他批(self, sample_job: Job) -> None:
        """20 筆分兩批，第一批被限流不該讓第二批也沒摘要。"""
        respx.post(_API_URL).mock(
            side_effect=[httpx.Response(429), _completion(_entries(5, label_start=10))]
        )

        results = await summarize_jobs(_jobs(sample_job, 15))

        assert results[0].summary_description == sample_job.description  # 第一批降級
        assert results[14].summary_description == "工作14"  # 第二批正常


class TestWholeBatchFallback:
    """整批失敗都要降級成截斷原文，不能拋例外中斷日報。"""

    @respx.mock
    async def test_HTTP_錯誤降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=httpx.Response(500))

        results = await summarize_jobs([sample_job])

        assert results[0].summary_description == sample_job.description
        assert results[0].is_summarized is True  # 仍算已處理，不會被下游當成漏網

    @respx.mock
    async def test_網路錯誤降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(side_effect=httpx.ConnectError("連不上"))

        results = await summarize_jobs([sample_job])

        assert results[0].summary_description == sample_job.description

    @respx.mock
    async def test_回應不是_JSON_降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=_completion("我覺得這些職缺都不錯"))

        results = await summarize_jobs([sample_job])

        assert results[0].summary_description == sample_job.description

    @respx.mock
    async def test_回應是物件而非陣列時降級(self, sample_job: Job) -> None:
        """模型偶爾會擅自包一層 {"jobs": [...]}。"""
        respx.post(_API_URL).mock(return_value=_completion('{"jobs": []}'))

        results = await summarize_jobs([sample_job])

        assert results[0].summary_description == sample_job.description

    @respx.mock
    async def test_回應結構不符降級為原文(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=httpx.Response(200, json={"choices": []}))

        results = await summarize_jobs([sample_job])

        assert results[0].summary_description == sample_job.description

    @respx.mock
    async def test_模型用_code_fence_包住仍能解析(self, sample_job: Job) -> None:
        """實測不少模型會擅自加上 ```json 標記。"""
        respx.post(_API_URL).mock(return_value=_completion(f"```json\n{_entries(1)}\n```"))

        results = await summarize_jobs([sample_job])

        assert results[0].summary_description == "工作0"

    @respx.mock
    async def test_過長原文會被截斷並加省略號(self, sample_job: Job) -> None:
        respx.post(_API_URL).mock(return_value=httpx.Response(500))
        long_job = replace(sample_job, description="字" * 500)

        results = await summarize_jobs([long_job])

        assert results[0].summary_description.endswith("…")
        assert len(results[0].summary_description) == 121  # 120 字 + 省略號

    @respx.mock
    async def test_福利空白時填未列出(self, sample_job: Job) -> None:
        """日報欄位固定五項，空字串會讓訊息看起來像壞掉。"""
        respx.post(_API_URL).mock(return_value=httpx.Response(500))
        no_benefit = replace(sample_job, benefit="   ")

        results = await summarize_jobs([no_benefit])

        assert results[0].summary_benefit == "未列出"


class TestRequestShape:
    @respx.mock
    async def test_送出的請求帶金鑰與模型名(self, sample_job: Job) -> None:
        route = respx.post(_API_URL).mock(return_value=_completion(_entries(1)))

        await summarize_jobs([sample_job])

        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer test-key"
        assert b"test-model" in request.content
        assert b"messages" in request.content

    @respx.mock
    async def test_原始_job_不被修改(self, sample_job: Job) -> None:
        """frozen dataclass 的保證，但這是跨模組資料流的關鍵前提，值得明測。"""
        respx.post(_API_URL).mock(return_value=_completion(_entries(1)))

        results = await summarize_jobs([sample_job])

        assert sample_job.summary_description is None
        assert results[0] is not sample_job


class TestClientOwnership:
    @respx.mock
    async def test_注入的_client_不會被關掉(self, sample_job: Job) -> None:
        """呼叫端可能還要用同一個 client，被關掉的話後續全部失敗。"""
        respx.post(_API_URL).mock(return_value=_completion(_entries(1)))

        async with httpx.AsyncClient() as client:
            await summarize_jobs([sample_job], client=client)
            assert client.is_closed is False
