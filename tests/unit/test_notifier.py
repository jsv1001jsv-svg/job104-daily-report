"""LINE 訊息排版測試。不打真實 API。"""

from dataclasses import replace
from datetime import UTC, datetime

from src.models import DailyReport, Job
from src.notifier.line import _build_messages, _format_job


def _report(jobs: tuple[Job, ...]) -> DailyReport:
    return DailyReport(
        user_id="U1",
        report_date=datetime(2026, 8, 1, tzinfo=UTC),
        jobs=jobs,
    )


class TestFormatJob:
    def test_包含日報要求的所有欄位(self, sample_job: Job) -> None:
        text = _format_job(1, sample_job)

        assert sample_job.title in text
        assert sample_job.company in text
        assert sample_job.location in text
        assert sample_job.salary in text
        assert sample_job.url in text

    def test_有摘要時優先顯示摘要(self, sample_job: Job) -> None:
        summarized = sample_job.with_summary("摘要內容", "摘要條件", "摘要福利")
        text = _format_job(1, summarized)

        assert "摘要內容" in text
        assert sample_job.description not in text

    def test_無摘要時退回原文(self, sample_job: Job) -> None:
        text = _format_job(1, sample_job)
        assert sample_job.description in text


class TestBuildMessages:
    def test_少量職缺合併成單則訊息(self, sample_job: Job) -> None:
        messages = _build_messages(_report((sample_job, sample_job)))
        assert len(messages) == 1

    def test_標頭顯示職缺筆數(self, sample_job: Job) -> None:
        messages = _build_messages(_report((sample_job,) * 3))
        assert "3 筆新職缺" in messages[0]

    def test_超長內容會自動分成多則(self, sample_job: Job) -> None:
        # 單筆撐大到接近上限，20 筆必然超過單則字數限制
        fat_job = replace(sample_job, description="工作內容說明" * 100)
        messages = _build_messages(_report((fat_job,) * 20))

        assert len(messages) > 1
        assert all(len(m) <= 4800 for m in messages)

    def test_每則訊息都不是空的(self, sample_job: Job) -> None:
        messages = _build_messages(_report((sample_job,) * 20))
        assert all(m.strip() for m in messages)
