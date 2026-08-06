"""本機排程器：模擬正式環境的 Modal Cron。

只在 Docker Compose 的 scheduler 服務裡跑。正式環境不用這個檔案。

手動立刻觸發一次（不用等到 9 點）：
    docker compose exec scheduler python -c \
      "import asyncio; from src.pipeline import run_daily_report; \
       print(asyncio.run(run_daily_report()))"
"""

import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_settings
from src.pipeline import run_daily_report

logger = logging.getLogger(__name__)

# 本機想馬上看到結果時，設 RUN_ON_STARTUP=1 讓容器啟動就跑一次
_RUN_ON_STARTUP = os.getenv("RUN_ON_STARTUP") == "1"


async def _job() -> None:
    """排程觸發的工作。包一層 try 避免例外殺掉整個排程器。"""
    logger.info("排程觸發：開始產生每日日報")
    try:
        stats = await run_daily_report()
        logger.info("每日日報完成：%s", stats)
    except Exception:
        logger.exception("每日日報執行失敗")


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 缺金鑰就別啟動 —— 這個容器的存在意義是早上 9:00 推播，
    # 等到那一刻才發現金鑰沒設，等於白白損失一天。寧可現在就起不來。
    settings.require_production_secrets()

    scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
    scheduler.add_job(
        _job,
        # 每天 09:00 Asia/Taipei，平日與週末皆執行
        CronTrigger(hour=9, minute=0, timezone="Asia/Taipei"),
        id="daily_report",
        max_instances=1,          # 上一輪還沒跑完就不重複啟動
        misfire_grace_time=3600,  # 容器晚啟動一小時內仍補跑
    )
    scheduler.start()
    logger.info("排程器已啟動，每日 09:00 (Asia/Taipei) 執行")

    if _RUN_ON_STARTUP:
        logger.info("RUN_ON_STARTUP=1，立即執行一次")
        await _job()

    # 保持行程存活，讓排程器持續運作
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
