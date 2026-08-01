"""用 OpenRouter 上的便宜模型把職缺描述濃縮成固定欄位。

摘要失敗時「不」讓整份日報失敗 —— 退回使用原始文字截斷版，
使用者收到品質稍差的日報，好過收不到日報。
"""

import json
import logging

import httpx

from src.config import get_settings
from src.models import Job

logger = logging.getLogger(__name__)

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_FALLBACK_LENGTH = 120

_PROMPT = """你是職缺摘要助手。請把以下 104 職缺資訊濃縮成三個欄位，回傳 JSON。

要求：
- description：工作內容，繁體中文，60 字內
- requirement：應徵條件，繁體中文，60 字內
- benefit：福利，繁體中文，40 字內；原文沒提到就填「未列出」
- 只回傳 JSON，不要有其他文字或 markdown 標記

職缺標題：{title}
公司：{company}
工作內容：{description}
應徵條件：{requirement}
福利：{benefit}
"""


async def summarize(job: Job, client: httpx.AsyncClient | None = None) -> Job:
    """回傳帶摘要的新 Job。失敗時回傳截斷原文的版本，不拋例外。

    Args:
        job: 待摘要的職缺。
        client: 可注入的 httpx client，測試時 mock 用。

    Returns:
        帶 summary_* 欄位的新 Job 物件。
    """
    settings = get_settings()
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)

    try:
        response = await client.post(
            _API_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={
                "model": settings.openrouter_model,
                "messages": [{
                    "role": "user",
                    "content": _PROMPT.format(
                        title=job.title,
                        company=job.company,
                        description=job.description,
                        requirement=job.requirement,
                        benefit=job.benefit,
                    ),
                }],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_code_fence(content))

        return job.with_summary(
            description=parsed["description"],
            requirement=parsed["requirement"],
            benefit=parsed["benefit"],
        )
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
        # 這裡刻意吞掉例外並降級，但一定要留 log —— 不做靜默失敗
        logger.warning("摘要失敗，改用原文截斷 job_id=%s：%s", job.job_id, exc)
        return _fallback_summary(job)
    finally:
        if owns_client:
            await client.aclose()


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
