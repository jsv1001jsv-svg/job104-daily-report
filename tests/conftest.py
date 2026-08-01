"""共用測試 fixture。"""

from datetime import UTC, datetime

import pytest

from src.models import Job, UserConfig


@pytest.fixture
def sample_job() -> Job:
    return Job(
        job_id="12345",
        title="後端工程師",
        company="測試科技",
        url="https://www.104.com.tw/job/abcde",
        location="台北市內湖區",
        salary="月薪 60,000~90,000 元",
        description="開發與維護後端 API 服務",
        requirement="熟悉 Python 三年以上",
        benefit="彈性上下班、年終獎金",
    )


@pytest.fixture
def sample_user() -> UserConfig:
    return UserConfig(
        user_id="U_test_user",
        raw_query="台北 後端工程師",
        keyword="後端工程師",
        area_code="6001001000",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
