"""LINE Messaging API 推播。

注意：LINE Notify 已於 2025/3/31 停用，本專案用 Messaging API 的 push message。
免費方案每月推播則數有限（見 CLAUDE.md 第 5 節），超量會被拒送。
"""

import logging

import httpx

from src.config import get_settings
from src.models import DailyReport, Job

logger = logging.getLogger(__name__)

_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# LINE 單則訊息上限 5000 字，單次 push 最多 5 則訊息
_MAX_CHARS_PER_MESSAGE = 4800
_MAX_MESSAGES_PER_PUSH = 5

_EMPTY_MESSAGE = "📭 今日已無新職缺。\n明天早上 9:00 再為你查一次。"


class NotifierError(RuntimeError):
    """推播失敗。呼叫端應記錄並繼續處理其他使用者。"""


async def push_report(report: DailyReport, client: httpx.AsyncClient | None = None) -> None:
    """把日報推給指定使用者。

    Args:
        report: 單一使用者的當日日報。
        client: 可注入的 httpx client，測試時 mock 用。

    Raises:
        NotifierError: LINE API 回非 2xx 或網路錯誤。
    """
    settings = get_settings()
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=20.0)

    texts = [_EMPTY_MESSAGE] if report.is_empty else _build_messages(report)

    try:
        response = await client.post(
            _PUSH_URL,
            headers={
                "Authorization": f"Bearer {settings.line_channel_access_token}",
                "Content-Type": "application/json",
            },
            json={
                "to": report.user_id,
                "messages": [{"type": "text", "text": t} for t in texts[:_MAX_MESSAGES_PER_PUSH]],
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NotifierError(f"LINE 推播失敗 user_id={report.user_id}：{exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    logger.info("推播完成 user_id=%s 職缺數=%d", report.user_id, len(report.jobs))


def _build_messages(report: DailyReport) -> list[str]:
    """把職缺排版成文字訊息，超過單則字數上限就自動分則。"""
    header = f"☀️ {report.report_date:%m/%d} 職缺日報\n找到 {len(report.jobs)} 筆新職缺\n"
    messages: list[str] = []
    current = header

    for index, job in enumerate(report.jobs, start=1):
        block = _format_job(index, job)
        if len(current) + len(block) > _MAX_CHARS_PER_MESSAGE:
            messages.append(current.rstrip())
            current = ""
        current += block

    if current.strip():
        messages.append(current.rstrip())

    return messages


def _format_job(index: int, job: Job) -> str:
    """單筆職缺的顯示格式，欄位依 CLAUDE.md 第 1 節要求。"""
    return (
        f"\n──────────\n"
        f"{index}. {job.title}\n"
        f"🏢 {job.company}\n"
        f"📍 {job.location}\n"
        f"💰 {job.salary}\n"
        f"📋 工作內容：{job.summary_description or job.description}\n"
        f"✅ 條件：{job.summary_requirement or job.requirement}\n"
        f"🎁 福利：{job.summary_benefit or job.benefit}\n"
        f"🔗 {job.url}\n"
    )
