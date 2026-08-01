"""領域模型測試：重點是不可變性。"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from src.models import DailyReport, Job


class TestJob:
    def test_不能直接修改欄位(self, sample_job: Job) -> None:
        with pytest.raises(FrozenInstanceError):
            sample_job.title = "改掉"  # type: ignore[misc]

    def test_剛抓到的職缺尚未摘要(self, sample_job: Job) -> None:
        assert sample_job.is_summarized is False

    def test_with_summary_產生新物件且不動原物件(self, sample_job: Job) -> None:
        summarized = sample_job.with_summary("內容", "條件", "福利")

        assert summarized is not sample_job
        assert summarized.is_summarized is True
        assert summarized.summary_description == "內容"
        # 原物件必須完全不受影響
        assert sample_job.summary_description is None
        assert sample_job.is_summarized is False

    def test_with_summary_保留原有欄位(self, sample_job: Job) -> None:
        summarized = sample_job.with_summary("內容", "條件", "福利")
        assert summarized.job_id == sample_job.job_id
        assert summarized.description == sample_job.description


class TestDailyReport:
    def test_沒有職缺時視為空日報(self) -> None:
        report = DailyReport(user_id="U1", report_date=datetime.now(UTC), jobs=())
        assert report.is_empty is True

    def test_有職缺時不是空日報(self, sample_job: Job) -> None:
        report = DailyReport(user_id="U1", report_date=datetime.now(UTC), jobs=(sample_job,))
        assert report.is_empty is False
