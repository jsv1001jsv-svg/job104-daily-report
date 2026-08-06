"""每日日報主流程測試。

外部相依（104、OpenRouter、LINE、Firestore）全部換成假的 ——
這裡要驗的是**編排**：誰先誰後、哪一步失敗會怎樣。

最關鍵的兩條規則，各自有專門的測試：
  1. 單一使用者失敗不影響其他使用者
  2. 推播成功才標記已看過（順序反了會導致職缺永久遺失）
"""

from dataclasses import replace
from typing import Any

import pytest

from src.config import get_settings
from src.models import Job, UserConfig
from src.notifier.line import NotifierError
from src.pipeline import run_daily_report
from src.scraper.client import ScraperError


class FakeStore:
    """記錄呼叫的假 JobStore。預設所有職缺都是新的。"""

    def __init__(self, users: list[UserConfig], seen: set[str] | None = None) -> None:
        self._users = users
        self._seen = seen or set()
        self.marked: dict[str, list[Job]] = {}

    async def list_users(self) -> list[UserConfig]:
        return self._users

    async def filter_unseen(self, user_id: str, jobs: list[Job]) -> list[Job]:
        return [job for job in jobs if job.job_id not in self._seen]

    async def mark_seen(self, user_id: str, jobs: list[Job]) -> None:
        self.marked[user_id] = jobs


class FakeHttpSession:
    """記錄是否被啟動的假 HTTP session。"""

    started = 0

    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> "FakeHttpSession":
        FakeHttpSession.started += 1
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _fake_session(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpSession.started = 0
    monkeypatch.setattr("src.pipeline.HttpSession", FakeHttpSession)


@pytest.fixture
def pushed() -> list[Any]:
    return []


@pytest.fixture(autouse=True)
def _fake_externals(monkeypatch: pytest.MonkeyPatch, pushed: list[Any], sample_job: Job) -> None:
    """抓取回兩筆、摘要原樣回傳、推播成功並記錄。個別測試可再覆寫。"""

    async def fake_fetch(_session: Any, **_kwargs: Any) -> list[Job]:
        return [replace(sample_job, job_id="j1"), replace(sample_job, job_id="j2")]

    async def fake_summarize_jobs(jobs: Any) -> list[Job]:
        return [job.with_summary("摘要內容", "摘要條件", "摘要福利") for job in jobs]

    async def fake_push(report: Any) -> None:
        pushed.append(report)

    monkeypatch.setattr("src.pipeline.fetch_jobs", fake_fetch)
    monkeypatch.setattr("src.pipeline.summarize_jobs", fake_summarize_jobs)
    monkeypatch.setattr("src.pipeline.push_report", fake_push)


def _user(user_id: str) -> UserConfig:
    from datetime import UTC, datetime

    return UserConfig(
        user_id=user_id,
        raw_query="台北 後端工程師",
        keyword="後端工程師",
        area_code="6001001000",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


class TestNoUsers:
    async def test_沒有使用者時回傳零統計(self) -> None:
        stats = await run_daily_report(store=FakeStore([]))

        assert stats == {"total": 0, "succeeded": 0, "failed": 0}

    async def test_沒有使用者時不建立連線(self) -> None:
        """沒人訂閱就不必建立連線。"""
        await run_daily_report(store=FakeStore([]))

        assert FakeHttpSession.started == 0


class TestHappyPath:
    async def test_成功流程的統計(self) -> None:
        stats = await run_daily_report(store=FakeStore([_user("U1")]))

        assert stats == {"total": 1, "succeeded": 1, "failed": 0}

    async def test_推播內容含摘要後的職缺(self, pushed: list[Any]) -> None:
        await run_daily_report(store=FakeStore([_user("U1")]))

        assert len(pushed) == 1
        assert pushed[0].user_id == "U1"
        assert all(job.is_summarized for job in pushed[0].jobs)

    async def test_推播成功後標記已看過(self) -> None:
        store = FakeStore([_user("U1")])

        await run_daily_report(store=store)

        assert [job.job_id for job in store.marked["U1"]] == ["j1", "j2"]

    async def test_整批共用一個_session(self) -> None:
        """每位使用者各開一條連線是浪費，且更像異常流量。"""
        store = FakeStore([_user("U1"), _user("U2"), _user("U3")])

        await run_daily_report(store=store)

        assert FakeHttpSession.started == 1

    async def test_已看過的職缺不再推送(self, pushed: list[Any]) -> None:
        store = FakeStore([_user("U1")], seen={"j1"})

        await run_daily_report(store=store)

        assert [job.job_id for job in pushed[0].jobs] == ["j2"]

    async def test_沒有新職缺仍會推播(self, pushed: list[Any]) -> None:
        """空日報也要推「今日已無新職缺」，讓使用者知道系統還活著。"""
        store = FakeStore([_user("U1")], seen={"j1", "j2"})

        await run_daily_report(store=store)

        assert len(pushed) == 1
        assert pushed[0].is_empty


class TestPerUserIsolation:
    async def test_一位使用者抓取失敗不影響其他人(
        self, monkeypatch: pytest.MonkeyPatch, pushed: list[Any], sample_job: Job
    ) -> None:
        async def selective_fetch(_session: Any, *, keyword: str, **_kwargs: Any) -> list[Job]:
            if keyword == "會壞的":
                raise ScraperError("Cloudflare 擋下")
            return [replace(sample_job, job_id="j1")]

        monkeypatch.setattr("src.pipeline.fetch_jobs", selective_fetch)
        broken = replace(_user("U_壞"), keyword="會壞的")
        store = FakeStore([broken, _user("U_好")])

        stats = await run_daily_report(store=store)

        assert stats == {"total": 2, "succeeded": 1, "failed": 1}
        assert [report.user_id for report in pushed] == ["U_好"]

    async def test_抓取失敗不推播也不標記(
        self, monkeypatch: pytest.MonkeyPatch, pushed: list[Any]
    ) -> None:
        async def always_fail(*_args: Any, **_kwargs: Any) -> list[Job]:
            raise ScraperError("抓不到")

        monkeypatch.setattr("src.pipeline.fetch_jobs", always_fail)
        store = FakeStore([_user("U1")])

        stats = await run_daily_report(store=store)

        assert stats["failed"] == 1
        assert pushed == []
        assert store.marked == {}


class TestPushFailure:
    async def test_推播失敗不標記已看過以便明天重試(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """順序反過來的話，推播失敗的那批職缺就永遠不會再出現。"""

        async def failing_push(_report: Any) -> None:
            raise NotifierError("LINE 額度用完")

        monkeypatch.setattr("src.pipeline.push_report", failing_push)
        store = FakeStore([_user("U1")])

        stats = await run_daily_report(store=store)

        assert stats["failed"] == 1
        assert store.marked == {}


class TestDailyLimit:
    async def test_每日筆數上限(
        self, monkeypatch: pytest.MonkeyPatch, pushed: list[Any], sample_job: Job
    ) -> None:
        async def many_jobs(*_args: Any, **_kwargs: Any) -> list[Job]:
            return [replace(sample_job, job_id=f"j{i}") for i in range(50)]

        monkeypatch.setattr("src.pipeline.fetch_jobs", many_jobs)
        monkeypatch.setenv("MAX_JOBS_PER_DAY", "3")
        get_settings.cache_clear()

        try:
            await run_daily_report(store=FakeStore([_user("U1")]))
            assert len(pushed[0].jobs) == 3
        finally:
            get_settings.cache_clear()

    async def test_只標記實際推送的那幾筆(
        self, monkeypatch: pytest.MonkeyPatch, sample_job: Job
    ) -> None:
        """抓了 50 筆但只推 3 筆，剩下 47 筆明天還要推。"""

        async def many_jobs(*_args: Any, **_kwargs: Any) -> list[Job]:
            return [replace(sample_job, job_id=f"j{i}") for i in range(50)]

        monkeypatch.setattr("src.pipeline.fetch_jobs", many_jobs)
        monkeypatch.setenv("MAX_JOBS_PER_DAY", "3")
        get_settings.cache_clear()
        store = FakeStore([_user("U1")])

        try:
            await run_daily_report(store=store)
            assert len(store.marked["U1"]) == 3
        finally:
            get_settings.cache_clear()
