"""104 職缺抓取。

⚠️ 尚未對真實 API 驗證：下方 endpoint、參數名、回應欄位路徑都是初版推測，
   必須實際打一次 104 搜尋頁、觀察 DevTools Network 後修正。
   驗證前請勿當成可用程式碼。驗證進度記錄於 CLAUDE.md 的「開發紀錄」。

當好公民的原則（寫在程式裡而非只寫在文件裡）：
  - 帶可辨識的 User-Agent 與 Referer
  - 每次請求之間有間隔（settings.scrape_delay_seconds）
  - 一天只跑一次，且有筆數上限
"""

import asyncio
import logging

import httpx

from src.config import get_settings
from src.models import Job

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.104.com.tw/jobs/search/api/jobs"
_JOB_DETAIL_URL = "https://www.104.com.tw/job/ajax/content/{job_id}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    # 104 的 JSON endpoint 會擋掉沒有 Referer 的請求
    "Referer": "https://www.104.com.tw/jobs/search/",
}


class ScraperError(RuntimeError):
    """抓取失敗。呼叫端應記錄並跳過該使用者，不要讓整批日報中斷。"""


async def search_jobs(
    keyword: str,
    area_code: str,
    limit: int,
    client: httpx.AsyncClient | None = None,
) -> list[Job]:
    """依關鍵字與地區搜尋職缺。

    Args:
        keyword: 職務關鍵字，例：「後端工程師」。
        area_code: 104 area 參數，見 area_map。
        limit: 最多回傳幾筆。
        client: 可注入的 httpx client，測試時用來 mock。

    Returns:
        Job 清單，摘要欄位為 None（尚未經過 summarizer）。

    Raises:
        ScraperError: 網路錯誤或回應格式非預期。
    """
    settings = get_settings()
    owns_client = client is None
    client = client or httpx.AsyncClient(headers=_HEADERS, timeout=20.0)

    params = {
        "ro": "0",              # 0 = 不限全職/兼職
        "keyword": keyword,
        "area": area_code,
        "order": "11",          # 依更新日期排序，新職缺優先
        "asc": "0",
        "page": "1",
        "mode": "s",
    }

    try:
        response = await client.get(_SEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise ScraperError(f"104 搜尋請求失敗（keyword={keyword}）：{exc}") from exc
    except ValueError as exc:
        raise ScraperError(f"104 回應不是合法 JSON（keyword={keyword}）：{exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    raw_jobs = _extract_job_list(payload)
    jobs = [_to_job(raw) for raw in raw_jobs[:limit]]

    logger.info("搜尋完成 keyword=%s area=%s 取得 %d 筆", keyword, area_code, len(jobs))
    await asyncio.sleep(settings.scrape_delay_seconds)
    return jobs


def _extract_job_list(payload: dict) -> list[dict]:
    """從 104 回應中取出職缺陣列。

    ⚠️ 路徑待驗證：實際結構可能是 data.list 或 data.job 等。
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ScraperError(f"回應缺少 data 物件：{list(payload)}")

    jobs = data.get("list")
    if not isinstance(jobs, list):
        raise ScraperError(f"回應 data 缺少 list 陣列：{list(data)}")

    return jobs


def _to_job(raw: dict) -> Job:
    """把 104 原始欄位轉成領域模型。

    ⚠️ 欄位名待驗證。集中在這一個函式，之後修正只需改這裡。
    """
    return Job(
        job_id=str(raw.get("jobNo", "")),
        title=raw.get("jobName", ""),
        company=raw.get("custName", ""),
        url=_normalize_url(raw.get("link", {}).get("job", "")),
        location=raw.get("jobAddrNoDesc", ""),
        salary=raw.get("salaryDesc", ""),
        description=raw.get("description", ""),
        requirement="",   # 搜尋列表沒有，需另打 job detail endpoint
        benefit="",       # 同上
    )


def _normalize_url(link: str) -> str:
    """104 回傳的連結常是 //www.104.com.tw/job/xxx 形式，補上 scheme。"""
    if link.startswith("//"):
        return f"https:{link}"
    return link
