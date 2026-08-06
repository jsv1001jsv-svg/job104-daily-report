"""手動把一位使用者寫進 Firestore，供本機端到端測試用。

**為什麼需要這支腳本**：正式流程裡使用者是透過 LINE webhook 註冊的
（加好友 → 傳搜尋條件），但 webhook 需要一個**公開的 HTTPS 網址**，
LINE 打不到 localhost。而日報主流程本身不需要 webhook —— 它只讀
Firestore 裡的使用者。所以先手動塞一筆，就能把「抓取 → 去重 → 摘要 →
推播」整條驗證完，把「架設公開網址」這件事留到第二階段。

你自己的 userId 在 LINE Developers Console 的 Basic settings 分頁
（`Your user ID`，U 開頭 33 字元）。見 docs/setup/credentials.md。

用法（在容器內執行，才連得到 Firestore 模擬器）：

    docker compose exec api python -m scripts.seed_user U你的userId "台北 後端工程師"
"""

import asyncio
import sys
from datetime import UTC, datetime

from src.models import UserConfig
from src.scraper.area_map import parse_query
from src.store.firestore import JobStore


async def seed(user_id: str, raw_query: str) -> None:
    """把 `地區 職務` 拆成 104 參數後寫入 Firestore。

    刻意重用 `parse_query()` —— webhook 收到使用者訊息時走的是同一支，
    這裡若自己另寫一套拆解邏輯，測出來的結果就不代表正式流程。
    """
    try:
        area_code, keyword = parse_query(raw_query)
    except ValueError as exc:
        raise SystemExit(f"條件無法解析：{exc}") from exc

    config = UserConfig(
        user_id=user_id,
        raw_query=raw_query,
        keyword=keyword,
        area_code=area_code,
        created_at=datetime.now(UTC),
    )
    await JobStore().upsert_user(config)

    print(f"已寫入使用者 {user_id}")
    print(f"  原始條件：{raw_query}")
    print(f"  keyword ：{keyword}")
    print(f"  area    ：{area_code}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"用法：python -m scripts.seed_user <userId> <地區 職務>\n{__doc__}")

    asyncio.run(seed(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
