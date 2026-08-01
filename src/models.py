"""領域模型：跨模組傳遞的資料結構。

全部 frozen（不可變）—— 建立後不能改，要變就產生新物件。
這樣資料流過 scraper → summarizer → notifier 時，不會有哪一層偷偷改到上游資料。
"""

from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UserConfig:
    """一位使用者的訂閱設定。對應 Firestore 的 users/{user_id}。"""

    user_id: str          # LINE userId
    raw_query: str        # 使用者原始輸入，例：「台北 後端工程師」
    keyword: str          # 解析出的職務關鍵字
    area_code: str        # 對應的 104 area 參數
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Job:
    """一筆 104 職缺。

    summary 系列欄位由 LLM 產生，抓取當下為 None，
    經 summarizer 處理後用 with_summary() 產生新物件。
    """

    job_id: str
    title: str
    company: str
    url: str
    location: str
    salary: str
    description: str      # 原始工作內容
    requirement: str      # 原始應徵條件
    benefit: str          # 原始福利說明

    summary_description: str | None = None
    summary_requirement: str | None = None
    summary_benefit: str | None = None

    @property
    def is_summarized(self) -> bool:
        return self.summary_description is not None

    def with_summary(
        self,
        description: str,
        requirement: str,
        benefit: str,
    ) -> "Job":
        """回傳帶摘要的新 Job，原物件不變。"""
        return replace(
            self,
            summary_description=description,
            summary_requirement=requirement,
            summary_benefit=benefit,
        )


@dataclass(frozen=True, slots=True)
class DailyReport:
    """單一使用者的當日日報，notifier 的輸入。"""

    user_id: str
    report_date: datetime
    jobs: tuple[Job, ...]

    @property
    def is_empty(self) -> bool:
        """沒有新職缺時，推播「今日已無新職缺」而非不推。"""
        return len(self.jobs) == 0
