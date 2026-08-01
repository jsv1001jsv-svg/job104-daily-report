"""104 抓取的對外入口：組合瀏覽器傳輸與欄位解析。

三層分工，改動時只需動到對應那層：

    browser.py   傳輸  —— 開瀏覽器、通過 Cloudflare、把 JSON 拿到手
    client.py    解析  —— 把 104 的 JSON 轉成 Job（與傳輸方式無關）
    fetcher.py   流程  —— 搜尋 → 逐筆補詳情，並處理部分失敗

pipeline 只需要認識這一層。
"""

import asyncio
import logging

from src.config import get_settings
from src.models import Job
from src.scraper.browser import BrowserError, BrowserSession
from src.scraper.client import (
    ScraperError,
    build_search_params,
    job_detail_url,
    job_page_url,
    parse_detail_payload,
    parse_search_payload,
)

logger = logging.getLogger(__name__)


async def fetch_jobs(
    session: BrowserSession,
    keyword: str,
    area_code: str,
    limit: int,
    jobcat: str | None = None,
) -> list[Job]:
    """搜尋職缺並補齊每筆的條件與福利。

    session 由呼叫端提供並跨使用者共用 —— Cloudflare 通行證綁在 session 上，
    每人各開一次瀏覽器會慢好幾倍（見 browser.BrowserSession）。

    Args:
        session: 已啟動的瀏覽器 session。
        keyword: 職務關鍵字。
        area_code: 104 area 參數，見 area_map。
        limit: 最多取幾筆。
        jobcat: 104 職務分類代碼；有給就優先於 keyword。

    Returns:
        欄位齊全的 Job 清單。個別職缺補詳情失敗時**保留該筆**（條件與福利留空），
        不整批放棄 —— 少兩個欄位的職缺仍比漏掉這筆職缺對使用者有用。

    Raises:
        ScraperError: 搜尋階段失敗（沒有搜尋結果就沒有日報可做）。
    """
    jobs = await _search(session, keyword, area_code, limit, jobcat)
    if not jobs:
        logger.info("搜尋無結果 keyword=%s area=%s", keyword, area_code)
        return []

    return await _attach_details(session, jobs)


async def _search(
    session: BrowserSession,
    keyword: str,
    area_code: str,
    limit: int,
    jobcat: str | None,
) -> list[Job]:
    """搜尋階段。失敗直接往上拋，這是整個流程的前提。"""
    params = build_search_params(keyword, area_code, limit, jobcat)

    try:
        payload = await session.fetch_search(params)
    except BrowserError as exc:
        raise ScraperError(f"104 搜尋失敗（keyword={keyword} area={area_code}）：{exc}") from exc

    jobs = parse_search_payload(payload, limit)
    logger.info("搜尋完成 keyword=%s area=%s 取得 %d 筆", keyword, area_code, len(jobs))
    return jobs


async def _attach_details(session: BrowserSession, jobs: list[Job]) -> list[Job]:
    """逐筆補上「條件」與「福利」—— 搜尋列表沒有這兩欄，但日報需要。

    刻意用序列而非平行：一次噴 20 個請求對 104 不友善，也容易觸發風控。
    每筆之間間隔 settings.scrape_delay_seconds。
    """
    settings = get_settings()
    detailed: list[Job] = []
    failed = 0

    for index, job in enumerate(jobs):
        if index:
            await asyncio.sleep(settings.scrape_delay_seconds)

        try:
            detailed.append(await _fetch_detail(session, job.job_id))
        except (BrowserError, ScraperError):
            logger.warning("補詳情失敗，保留搜尋結果 job_id=%s", job.job_id, exc_info=True)
            detailed.append(job)
            failed += 1

    if failed:
        logger.warning("共 %d/%d 筆未能補上條件與福利", failed, len(jobs))
    return detailed


async def _fetch_detail(session: BrowserSession, job_id: str) -> Job:
    """抓單筆詳情。Referer 須指向該職缺頁本身，指向搜尋頁會被 104 擋。"""
    payload = await session.fetch_json(
        job_detail_url(job_id),
        referer=job_page_url(job_id),
    )
    return parse_detail_payload(job_id, payload)
