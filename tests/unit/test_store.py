"""Firestore Repository 測試。

用假的 Firestore client 而非模擬器 —— 這裡要驗的是「JobStore 如何把
領域模型翻譯成文件」，不是 Firestore 本身的行為。
對真實模擬器的驗證屬於整合測試，另外做（見 CLAUDE.md 第 11 節）。
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.models import Job, UserConfig
from src.store.firestore import JobStore

# --- 假的 Firestore ---------------------------------------------------------


class FakeDocument:
    """一份文件。`set` 記錄寫入內容，供測試斷言。"""

    def __init__(self, doc_id: str, data: dict[str, Any] | None = None) -> None:
        self.id = doc_id
        self._data = data or {}
        self._subcollections: dict[str, FakeCollection] = {}

    def to_dict(self) -> dict[str, Any]:
        return self._data

    async def set(self, data: dict[str, Any], merge: bool = False) -> None:
        self._data = {**self._data, **data} if merge else data

    def collection(self, name: str) -> "FakeCollection":
        return self._subcollections.setdefault(name, FakeCollection())


class FakeCollection:
    def __init__(self, documents: dict[str, dict[str, Any]] | None = None) -> None:
        self._documents = {
            doc_id: FakeDocument(doc_id, data) for doc_id, data in (documents or {}).items()
        }

    def document(self, doc_id: str) -> FakeDocument:
        return self._documents.setdefault(doc_id, FakeDocument(doc_id))

    async def stream(self) -> AsyncIterator[FakeDocument]:
        for doc in list(self._documents.values()):
            yield doc


class FakeBatch:
    """批次寫入。commit 前不生效 —— 與真實 batch 行為一致。"""

    def __init__(self) -> None:
        self.pending: list[tuple[FakeDocument, dict[str, Any]]] = []
        self.commit_count = 0

    def set(self, document: FakeDocument, data: dict[str, Any]) -> None:
        self.pending.append((document, data))

    async def commit(self) -> None:
        self.commit_count += 1
        for document, data in self.pending:
            document._data = data
        self.pending = []


class FakeFirestore:
    def __init__(self, users: dict[str, dict[str, Any]] | None = None) -> None:
        self.collections = {"users": FakeCollection(users)}
        self.batches: list[FakeBatch] = []

    def collection(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())

    def batch(self) -> FakeBatch:
        batch = FakeBatch()
        self.batches.append(batch)
        return batch


@pytest.fixture
def fake_db() -> FakeFirestore:
    return FakeFirestore()


@pytest.fixture
def store(fake_db: FakeFirestore) -> JobStore:
    return JobStore(client=fake_db)


# --- 測試 -------------------------------------------------------------------


class TestUpsertUser:
    async def test_寫入使用者條件的所有欄位(
        self, store: JobStore, fake_db: FakeFirestore, sample_user: UserConfig
    ) -> None:
        await store.upsert_user(sample_user)

        data = fake_db.collection("users").document(sample_user.user_id).to_dict()
        assert data["keyword"] == "後端工程師"
        assert data["area_code"] == "6001001000"
        assert data["raw_query"] == "台北 後端工程師"

    async def test_改條件會覆寫舊值(
        self, store: JobStore, fake_db: FakeFirestore, sample_user: UserConfig
    ) -> None:
        """使用者重傳一次條件就是要換條件，不能兩組並存。"""
        await store.upsert_user(sample_user)
        await store.upsert_user(replace(sample_user, keyword="前端工程師", raw_query="台北 前端"))

        data = fake_db.collection("users").document(sample_user.user_id).to_dict()
        assert data["keyword"] == "前端工程師"


class TestListUsers:
    async def test_回傳已設定條件的使用者(self) -> None:
        db = FakeFirestore({
            "U1": {
                "raw_query": "台北 後端工程師",
                "keyword": "後端工程師",
                "area_code": "6001001000",
                "created_at": datetime(2026, 8, 1, tzinfo=UTC),
            }
        })

        users = await JobStore(client=db).list_users()

        assert len(users) == 1
        assert users[0].user_id == "U1"
        assert users[0].keyword == "後端工程師"

    async def test_略過只加好友沒設條件的使用者(self) -> None:
        """加了好友但沒傳訊息的人沒有 keyword，排程要跳過而不是報錯。"""
        db = FakeFirestore({
            "U_有設定": {"keyword": "後端工程師"},
            "U_沒設定": {"created_at": datetime(2026, 8, 1, tzinfo=UTC)},
        })

        users = await JobStore(client=db).list_users()

        assert [u.user_id for u in users] == ["U_有設定"]

    async def test_缺少選填欄位時給預設值(self) -> None:
        """舊資料可能缺欄位，不該讓整批排程掛掉。"""
        db = FakeFirestore({"U1": {"keyword": "後端工程師"}})

        users = await JobStore(client=db).list_users()

        assert users[0].area_code == ""
        assert users[0].raw_query == ""
        assert users[0].created_at is not None

    async def test_沒有任何使用者回傳空清單(self, store: JobStore) -> None:
        assert await store.list_users() == []


class TestFilterUnseen:
    async def test_濾掉已看過的職缺(self, store: JobStore, sample_job: Job) -> None:
        seen = replace(sample_job, job_id="old")
        fresh = replace(sample_job, job_id="new")
        await store.mark_seen("U1", [seen])

        unseen = await store.filter_unseen("U1", [seen, fresh])

        assert [j.job_id for j in unseen] == ["new"]

    async def test_保持原本順序(self, store: JobStore, sample_job: Job) -> None:
        """104 的排序是「最新上架在前」，去重不該打亂它。"""
        jobs = [replace(sample_job, job_id=f"j{i}") for i in range(5)]

        unseen = await store.filter_unseen("U1", jobs)

        assert [j.job_id for j in unseen] == [f"j{i}" for i in range(5)]

    async def test_首次執行全部視為新職缺(self, store: JobStore, sample_job: Job) -> None:
        jobs = [replace(sample_job, job_id="a"), replace(sample_job, job_id="b")]

        assert len(await store.filter_unseen("U_新使用者", jobs)) == 2

    async def test_各使用者的已看清單互不影響(self, store: JobStore, sample_job: Job) -> None:
        """多使用者版的核心前提：A 看過的不代表 B 看過。"""
        await store.mark_seen("U_A", [sample_job])

        unseen = await store.filter_unseen("U_B", [sample_job])

        assert len(unseen) == 1


class TestMarkSeen:
    async def test_寫入職缺基本欄位供日後查閱(
        self, store: JobStore, fake_db: FakeFirestore, sample_job: Job
    ) -> None:
        await store.mark_seen("U1", [sample_job])

        doc = (
            fake_db.collection("users")
            .document("U1")
            .collection("seen_jobs")
            .document(sample_job.job_id)
        )
        assert doc.to_dict()["title"] == sample_job.title
        assert doc.to_dict()["url"] == sample_job.url
        assert doc.to_dict()["first_seen_at"] is not None

    async def test_多筆只送一次_batch(
        self, store: JobStore, fake_db: FakeFirestore, sample_job: Job
    ) -> None:
        """一次往返寫 20 筆，而不是 20 次往返。"""
        jobs = [replace(sample_job, job_id=f"j{i}") for i in range(20)]

        await store.mark_seen("U1", jobs)

        assert len(fake_db.batches) == 1
        assert fake_db.batches[0].commit_count == 1

    async def test_空清單不打資料庫(self, store: JobStore, fake_db: FakeFirestore) -> None:
        """沒有新職缺的日子是常態，不該產生無謂的寫入。"""
        await store.mark_seen("U1", [])

        assert fake_db.batches == []
