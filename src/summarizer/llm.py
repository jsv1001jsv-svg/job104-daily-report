"""用便宜的 LLM 把職缺描述濃縮成固定欄位。

**不綁定特定供應商**：只要是 OpenAI 相容的 `/chat/completions` 端點都能用，
換供應商只要改 `LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL` 三個環境變數。
預設是 Google AI Studio（免費額度不需信用卡，繁體中文品質穩定）；
OpenRouter、Groq、自架 LiteLLM 也都是同一套介面。

**一次請求處理多筆職缺**。這是為了額度而非效能 —— 免費層是以「請求數」
計限（5-15 RPM），一筆一請求的話 20 筆職缺會直接被限流擋掉。

摘要失敗時「不」讓整份日報失敗 —— 退回使用原始文字截斷版，
使用者收到品質稍差的日報，好過收不到日報。降級的顆粒度是「單筆」：
模型漏掉其中一筆時，只有那筆退回原文，其餘照常。
"""

import json
import logging
from collections.abc import Iterator, Sequence

import httpx

from src.config import get_settings
from src.models import Job

logger = logging.getLogger(__name__)

_FALLBACK_LENGTH = 120
_REQUIRED_FIELDS = ("description", "requirement", "benefit")

_PROMPT = """你是職缺摘要助手。以下是 {count} 筆 104 職缺，請逐筆濃縮成三個欄位。

要求：
- description：工作內容，繁體中文，60 字內
- requirement：應徵條件，繁體中文，60 字內
- benefit：福利，繁體中文，40 字內；原文沒提到就填「未列出」
- 回傳 JSON 陣列，每個元素包含 index、description、requirement、benefit
- index 必須對應下方職缺的編號，且每筆都要有，不可省略
- 只回傳 JSON 陣列，不要有其他文字或 markdown 標記

{jobs}
"""

_JOB_BLOCK = """[{index}]
職缺標題：{title}
公司：{company}
工作內容：{description}
應徵條件：{requirement}
福利：{benefit}
"""


async def summarize_jobs(
    jobs: Sequence[Job],
    client: httpx.AsyncClient | None = None,
) -> list[Job]:
    """回傳帶摘要的新 Job 清單，順序與輸入相同。不拋例外。

    依 `LLM_BATCH_SIZE` 分批，批次之間**依序**送出而非併發 ——
    併發會在同一秒內打出多個請求，正是免費層 RPM 限制要擋的行為。

    Args:
        jobs: 待摘要的職缺。
        client: 可注入的 httpx client，測試與呼叫端共用連線時用。

    Returns:
        與輸入等長、順序相同的 Job 清單，全部都有 summary_* 欄位。
    """
    if not jobs:
        return []

    settings = get_settings()
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=60.0)

    try:
        results: list[Job] = []
        for chunk in _chunks(jobs, settings.llm_batch_size):
            results.extend(await _summarize_chunk(chunk, client))
        return results
    finally:
        if owns_client:
            await client.aclose()


def _chunks(jobs: Sequence[Job], size: int) -> Iterator[Sequence[Job]]:
    for start in range(0, len(jobs), size):
        yield jobs[start : start + size]


async def _summarize_chunk(chunk: Sequence[Job], client: httpx.AsyncClient) -> list[Job]:
    """摘要一批職缺。整批失敗就整批降級，不往上拋。"""
    settings = get_settings()

    try:
        response = await client.post(
            settings.llm_api_url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": _build_prompt(chunk)}],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        entries = _parse_entries(content)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        # 這裡刻意吞掉例外並降級，但一定要留 log —— 不做靜默失敗
        logger.warning("整批摘要失敗，改用原文截斷（%d 筆）：%s", len(chunk), exc)
        return [_fallback_summary(job) for job in chunk]

    return [_apply_entry(job, entries.get(index)) for index, job in enumerate(chunk)]


def _build_prompt(chunk: Sequence[Job]) -> str:
    blocks = "\n".join(
        _JOB_BLOCK.format(
            index=index,
            title=job.title,
            company=job.company,
            description=job.description,
            requirement=job.requirement,
            benefit=job.benefit,
        )
        for index, job in enumerate(chunk)
    )
    return _PROMPT.format(count=len(chunk), jobs=blocks)


def _parse_entries(content: str) -> dict[int, dict[str, str]]:
    """把模型回應轉成 {index: 欄位}。不是陣列就當整批失敗。

    Raises:
        TypeError: 回應不是 JSON 陣列（模型偶爾會擅自包一層物件）。
        json.JSONDecodeError: 回應根本不是 JSON。
    """
    parsed = json.loads(_strip_code_fence(content))
    if not isinstance(parsed, list):
        raise TypeError(f"預期 JSON 陣列，收到 {type(parsed).__name__}")
    return {
        entry["index"]: entry
        for entry in parsed
        if isinstance(entry, dict) and "index" in entry
    }


def _apply_entry(job: Job, entry: dict[str, str] | None) -> Job:
    """套用單筆摘要。缺欄位就這一筆降級，不影響同批其他筆。"""
    if entry is None or any(not entry.get(field) for field in _REQUIRED_FIELDS):
        logger.warning("該筆摘要缺漏，改用原文截斷 job_id=%s", job.job_id)
        return _fallback_summary(job)

    return job.with_summary(
        description=entry["description"],
        requirement=entry["requirement"],
        benefit=entry["benefit"],
    )


def _strip_code_fence(content: str) -> str:
    """模型有時會用 ```json 包住回應，去掉才能 parse。"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    return text.strip()


def _fallback_summary(job: Job) -> Job:
    """LLM 不可用時的降級方案：直接截斷原文。"""
    return job.with_summary(
        description=_truncate(job.description),
        requirement=_truncate(job.requirement),
        benefit=_truncate(job.benefit) or "未列出",
    )


def _truncate(text: str, length: int = _FALLBACK_LENGTH) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= length else f"{cleaned[:length]}…"
