"""每日日報主流程：抓取 → 去重 → 摘要 → 推播 → 入庫。

這是「平台無關」的核心，不知道 Modal 或 Docker 存在。
兩個入口都呼叫這裡：
  - modal_app.py   正式環境，由 Modal Cron 每天 09:00 觸發
  - scheduler/run.py  本機開發，由 APScheduler 觸發

關鍵設計：單一使用者失敗不影響其他使用者。
一個人的搜尋條件打不到 104，不該讓其他人收不到日報。
"""

import asyncio
import logging
from datetime import UTC, datetime

from src.config import get_settings
from src.models import DailyReport, UserConfig
from src.notifier.line import NotifierError, push_report
from src.scraper.client import ScraperError
from src.scraper.fetcher import fetch_jobs
from src.scraper.transport import HttpSession
from src.store.firestore import JobStore
from src.summarizer.llm import summarize

logger = logging.getLogger(__name__)


async def run_daily_report(store: JobStore | None = None) -> dict[str, int]:
    """為所有已設定條件的使用者產生並推送日報。

    Args:
        store: 可注入的 JobStore，測試時 mock 用。

    Returns:
        執行統計 {"total": 使用者數, "succeeded": 成功數, "failed": 失敗數}。
    """
    store = store or JobStore()
    users = await store.list_users()
    logger.info("開始每日日報，共 %d 位使用者", len(users))

    if not users:
        return {"total": 0, "succeeded": 0, "failed": 0}

    # 整批共用一個 HTTP session，讓連線得以重用（見 CLAUDE.md 第 5.1 節）
    async with HttpSession() as session:
        results = await asyncio.gather(
            *(_process_user(user, store, session) for user in users),
            return_exceptions=True,
        )

    failed = sum(1 for r in results if isinstance(r, BaseException))
    stats = {"total": len(users), "succeeded": len(users) - failed, "failed": failed}
    logger.info("每日日報結束：%s", stats)
    return stats


async def _process_user(
    user: UserConfig,
    store: JobStore,
    session: HttpSession,
) -> None:
    """處理單一使用者。例外往上拋，由 gather 收集，不中斷其他人。

    Raises:
        ScraperError: 104 抓取失敗。
        NotifierError: LINE 推播失敗。
    """
    settings = get_settings()

    try:
        jobs = await fetch_jobs(
            session,
            keyword=user.keyword,
            area_code=user.area_code,
            limit=settings.max_jobs_per_day * 3,  # 多抓一些，去重後才夠數
        )
    except ScraperError:
        logger.exception("抓取失敗，略過此使用者 user_id=%s", user.user_id)
        raise

    unseen = await store.filter_unseen(user.user_id, jobs)
    selected = unseen[: settings.max_jobs_per_day]

    # 摘要平行處理；summarize 內部已處理失敗降級，不會拋例外
    summarized = await asyncio.gather(*(summarize(job) for job in selected))

    report = DailyReport(
        user_id=user.user_id,
        report_date=datetime.now(UTC),
        jobs=tuple(summarized),
    )

    try:
        await push_report(report)
    except NotifierError:
        logger.exception("推播失敗 user_id=%s，不寫入 seen_jobs 以便明天重試", user.user_id)
        raise

    # 推播成功才標記已看過 —— 順序反過來的話，推播失敗就永遠看不到這批職缺
    await store.mark_seen(user.user_id, selected)
