"""LINE 事件處理：follow（加好友）與 message（設定搜尋條件）。

使用者流程：
  1. 加官方帳號好友 → follow 事件 → 回歡迎訊息，請他傳搜尋條件
  2. 傳「台北 後端工程師」 → message 事件 → 解析並存進 Firestore
  3. 隔天早上 9:00 開始收到日報
"""

import logging
from datetime import UTC, datetime

import httpx

from src.config import get_settings
from src.models import UserConfig
from src.scraper.area_map import parse_query
from src.store.firestore import JobStore

logger = logging.getLogger(__name__)

_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

_WELCOME = (
    "👋 歡迎使用 104 每日職缺日報！\n\n"
    "請傳給我你想追蹤的「地區 + 職務」，例如：\n"
    "　台北 後端工程師\n"
    "　新北 AI 工程師\n\n"
    "設定後，每天早上 9:00 我會把新上架的職缺整理給你。"
)

_HELP = (
    "請用「地區 職務」的格式，例如：\n"
    "　台北 後端工程師\n\n"
    "支援六都與各縣市，可寫「台北」或「臺北市」。"
)


async def handle_event(event: dict) -> None:
    """分派 LINE 事件到對應處理函式。未支援的事件型別直接略過。"""
    event_type = event.get("type")

    if event_type == "follow":
        await _handle_follow(event)
    elif event_type == "message" and event.get("message", {}).get("type") == "text":
        await _handle_text_message(event)
    else:
        logger.debug("略過未處理的事件型別：%s", event_type)


async def _handle_follow(event: dict) -> None:
    """使用者加好友：回歡迎訊息，引導設定條件。"""
    user_id = event["source"]["userId"]
    logger.info("新使用者加入 user_id=%s", user_id)
    await _reply(event["replyToken"], _WELCOME)


async def _handle_text_message(event: dict) -> None:
    """使用者傳文字：當成搜尋條件解析並存檔。"""
    user_id = event["source"]["userId"]
    text = event["message"]["text"].strip()

    try:
        area_code, keyword = parse_query(text)
    except ValueError as exc:
        logger.info("條件解析失敗 user_id=%s text=%s：%s", user_id, text, exc)
        await _reply(event["replyToken"], f"{exc}\n\n{_HELP}")
        return

    config = UserConfig(
        user_id=user_id,
        raw_query=text,
        keyword=keyword,
        area_code=area_code,
        created_at=datetime.now(UTC),
    )
    await JobStore().upsert_user(config)

    await _reply(
        event["replyToken"],
        f"✅ 已設定搜尋條件：{text}\n明天早上 9:00 開始為你送上日報。",
    )


async def _reply(reply_token: str, text: str) -> None:
    """用 reply token 回覆訊息（不計入推播額度，比 push 省）。"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            _REPLY_URL,
            headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        )
        if response.status_code >= 400:
            logger.error("回覆訊息失敗 status=%s body=%s", response.status_code, response.text)
