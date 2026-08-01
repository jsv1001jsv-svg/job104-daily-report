"""Firestore 存取：使用者訂閱設定 + 各使用者已看過的職缺（去重用）。

Repository 模式 —— 所有 Firestore 細節關在這個模組裡，
其他模組只看得到 UserConfig / Job 這些領域模型。
之後若要換掉 Firestore，只需替換這個檔案。

資料結構（見 CLAUDE.md 第 7 節）：
    users/{user_id}
      ├─ raw_query, keyword, area_code, created_at
      └─ seen_jobs/{job_id}   子集合
            └─ title, company, url, first_seen_at
"""

import logging
from datetime import UTC, datetime

from google.cloud import firestore

from src.config import get_settings
from src.models import Job, UserConfig

logger = logging.getLogger(__name__)

_USERS = "users"
_SEEN_JOBS = "seen_jobs"


class JobStore:
    """使用者與已看職缺的資料存取。

    連線目標由環境決定：本機開發連 Firestore 模擬器
    （compose 帶入 FIRESTORE_EMULATOR_HOST），正式環境連雲端。
    """

    def __init__(self, client: firestore.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._client = client or firestore.AsyncClient(project=settings.firebase_project_id)
        if settings.use_emulator:
            logger.info("使用 Firestore 模擬器：%s", settings.firestore_emulator_host)

    # --- 使用者 ---

    async def upsert_user(self, config: UserConfig) -> None:
        """新增或更新使用者訂閱條件。使用者改條件時覆寫既有設定。"""
        await self._client.collection(_USERS).document(config.user_id).set(
            {
                "raw_query": config.raw_query,
                "keyword": config.keyword,
                "area_code": config.area_code,
                "created_at": config.created_at,
            },
            merge=True,
        )
        logger.info("已更新使用者設定 user_id=%s query=%s", config.user_id, config.raw_query)

    async def list_users(self) -> list[UserConfig]:
        """取得所有已設定條件的使用者，供每日排程逐一處理。

        尚未設定條件的使用者（只加好友沒傳訊息）會被略過。
        """
        users: list[UserConfig] = []
        async for doc in self._client.collection(_USERS).stream():
            data = doc.to_dict() or {}
            if not data.get("keyword"):
                continue
            users.append(
                UserConfig(
                    user_id=doc.id,
                    raw_query=data.get("raw_query", ""),
                    keyword=data["keyword"],
                    area_code=data.get("area_code", ""),
                    created_at=data.get("created_at", datetime.now(UTC)),
                )
            )
        return users

    # --- 去重 ---

    async def filter_unseen(self, user_id: str, jobs: list[Job]) -> list[Job]:
        """濾掉該使用者已看過的職缺，只留今天新出現的。

        Args:
            user_id: LINE userId。
            jobs: 本次抓到的所有職缺。

        Returns:
            未曾推送過的職缺，保持原順序。
        """
        seen = await self._load_seen_ids(user_id)
        unseen = [job for job in jobs if job.job_id not in seen]
        logger.info(
            "去重 user_id=%s 抓到 %d 筆，其中 %d 筆為新職缺",
            user_id, len(jobs), len(unseen),
        )
        return unseen

    async def mark_seen(self, user_id: str, jobs: list[Job]) -> None:
        """把已推送的職缺寫入 seen_jobs，下次不再推。

        用 batch 一次寫入，減少往返次數。
        """
        if not jobs:
            return

        batch = self._client.batch()
        collection = self._client.collection(_USERS).document(user_id).collection(_SEEN_JOBS)
        now = datetime.now(UTC)

        for job in jobs:
            batch.set(
                collection.document(job.job_id),
                {
                    "title": job.title,
                    "company": job.company,
                    "url": job.url,
                    "first_seen_at": now,
                },
            )

        await batch.commit()
        logger.info("已標記 %d 筆職缺為已看過 user_id=%s", len(jobs), user_id)

    async def _load_seen_ids(self, user_id: str) -> set[str]:
        collection = self._client.collection(_USERS).document(user_id).collection(_SEEN_JOBS)
        return {doc.id async for doc in collection.stream()}
