"""104 API 探測工具：打真實 endpoint，把原始回應印出來。

用途
----
104 沒有公開 API 文件，`src/scraper/client.py` 的 endpoint、參數名、回應欄位
最初都是推測。這支腳本負責「開一次 DevTools」——實際發請求、印出真實 JSON，
讓人（或 Claude）照著結果修正 client.py。

它不猜、不解析、不自動改程式碼，只做三件事：
  1. 依序試候選 endpoint，看哪個回 JSON
  2. 印出頂層結構與第一筆職缺的完整欄位
  3. 拿第一筆的 job_id 再打詳情 endpoint（工作內容／條件／福利在那裡）

原始回應會存成 .json，方便日後 diff 出 104 改版。

用法
----
    python scripts/probe_104_api.py
    python scripts/probe_104_api.py --keyword "AI 工程師" --area 6001002000
    python scripts/probe_104_api.py --out-dir docs/api-samples

當好公民：每個請求之間 sleep，總請求數個位數，跟人工開瀏覽器搜尋無異。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# --- 待驗證的候選 endpoint ---------------------------------------------------
# 依可信度排序：第一個是第三方實作佐證過的，第二個是 client.py 目前寫的。
# 哪個先回 200 + JSON 就是答案。
SEARCH_ENDPOINTS: tuple[str, ...] = (
    "https://www.104.com.tw/jobs/search/list",
    "https://www.104.com.tw/jobs/search/api/jobs",
)

DETAIL_ENDPOINT = "https://www.104.com.tw/job/ajax/content/{job_id}"

# 104 的 JSON endpoint 會擋掉沒有瀏覽器特徵的請求（實測直接 fetch 會 403）
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.104.com.tw/jobs/search/",
    "Accept": "application/json, text/plain, */*",
}

DEFAULT_KEYWORD = "後端工程師"
DEFAULT_AREA = "6001001000"  # 臺北市（待驗證）
REQUEST_DELAY_SECONDS = 2.0


def build_search_params(keyword: str, area: str) -> dict[str, str]:
    """組搜尋參數。參數名本身也是待驗證項目之一。"""
    return {
        "ro": "0",
        "keyword": keyword,
        "area": area,
        "order": "11",
        "asc": "0",
        "page": "1",
        "mode": "s",
        "jobsource": "2018indexpoc",
    }


async def probe_search(
    client: httpx.AsyncClient,
    keyword: str,
    area: str,
) -> tuple[str, dict[str, Any]] | None:
    """依序試候選 endpoint，回傳第一個成功的 (url, payload)。

    Returns:
        (實際可用的 url, 解析後的 JSON)；全部失敗時回 None。
    """
    params = build_search_params(keyword, area)

    for url in SEARCH_ENDPOINTS:
        print(f"\n{'=' * 70}")
        print(f"[搜尋] GET {url}")
        print(f"       params={params}")

        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            print(f"  ✗ 連線失敗：{exc}")
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            continue

        content_type = response.headers.get("content-type", "")
        print(f"  → HTTP {response.status_code}｜content-type: {content_type}")
        print(f"  → 實際請求網址：{response.url}")

        if response.status_code != 200:
            print(f"  ✗ 非 200，跳過。回應前 200 字：\n{response.text[:200]}")
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            continue

        try:
            payload = response.json()
        except ValueError:
            print("  ✗ 不是 JSON（可能是 HTML 頁面 → 該走 Playwright 或換 endpoint）")
            print(f"    回應前 300 字：\n{response.text[:300]}")
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            continue

        print("  ✓ 拿到 JSON")
        return str(url), payload

    return None


async def probe_detail(client: httpx.AsyncClient, job_id: str) -> dict[str, Any] | None:
    """打職缺詳情 endpoint。工作內容／條件／福利只有這裡才有。"""
    url = DETAIL_ENDPOINT.format(job_id=job_id)
    # 詳情 endpoint 的 Referer 需指向該職缺頁本身，指向搜尋頁會被擋
    headers = {**BASE_HEADERS, "Referer": f"https://www.104.com.tw/job/{job_id}"}

    print(f"\n{'=' * 70}")
    print(f"[詳情] GET {url}")

    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        print(f"  ✗ 連線失敗：{exc}")
        return None

    print(f"  → HTTP {response.status_code}")
    if response.status_code != 200:
        print(f"  ✗ 非 200。回應前 200 字：\n{response.text[:200]}")
        return None

    try:
        payload = response.json()
    except ValueError:
        print("  ✗ 不是 JSON")
        return None

    print("  ✓ 拿到 JSON")
    return payload


def describe(value: Any, indent: int = 0) -> None:
    """印出 JSON 結構骨架：只印 key 與型別，不印冗長內容。"""
    pad = "  " * indent
    if isinstance(value, dict):
        for key, sub in value.items():
            kind = type(sub).__name__
            if isinstance(sub, dict | list):
                size = len(sub)
                print(f"{pad}{key}: {kind}({size})")
                if indent < 2:
                    describe(sub, indent + 1)
            else:
                preview = str(sub)
                if len(preview) > 60:
                    preview = preview[:60] + "…"
                print(f"{pad}{key}: {kind} = {preview}")
    elif isinstance(value, list) and value:
        print(f"{pad}[0] ↓")
        describe(value[0], indent + 1)


def find_job_list(payload: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """在回應中找出職缺陣列，回傳 (路徑, 陣列)。

    不硬編死 data.list，改成掃描——這樣 104 改結構時腳本仍能指出新路徑。
    """
    candidates: list[tuple[str, list[Any]]] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > 3:
            return
        if isinstance(node, list) and node and isinstance(node[0], dict):
            candidates.append((path, node))
        elif isinstance(node, dict):
            for key, sub in node.items():
                walk(sub, f"{path}.{key}" if path else key, depth + 1)

    walk(payload, "", 0)
    if not candidates:
        return None
    # 最長的那個陣列最可能是職缺清單
    return max(candidates, key=lambda item: len(item[1]))


def save(payload: Any, out_dir: Path, name: str) -> Path:
    """存原始回應，供日後比對 104 是否改版。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}-{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run(keyword: str, area: str, out_dir: Path) -> int:
    """執行完整探測流程。回傳 process exit code。"""
    print(f"104 API 探測｜keyword={keyword!r} area={area}")

    async with httpx.AsyncClient(
        headers=BASE_HEADERS, timeout=20.0, follow_redirects=True
    ) as client:
        result = await probe_search(client, keyword, area)

        if result is None:
            print(f"\n{'=' * 70}")
            print("✗ 所有候選 endpoint 都失敗。")
            print("  下一步：開 F12 → Network → Fetch/XHR，手動搜尋一次，")
            print("         把回傳職缺清單的請求 Copy as cURL 貼給 Claude。")
            return 1

        url, payload = result
        saved = save(payload, out_dir, "search")

        print(f"\n{'-' * 70}")
        print("搜尋回應結構：")
        describe(payload)

        found = find_job_list(payload)
        if found is None:
            print("\n⚠ 找不到職缺陣列，請人工檢視存下來的 JSON。")
            print(f"  原始回應：{saved}")
            return 1

        path, jobs = found
        print(f"\n{'-' * 70}")
        print(f"職缺陣列路徑：{path}（共 {len(jobs)} 筆）")
        print("第一筆職缺的完整欄位：")
        print(json.dumps(jobs[0], ensure_ascii=False, indent=2))

        print(f"\n可用的搜尋 endpoint：{url}")
        print(f"原始回應已存：{saved}")

        job_id = _guess_job_id(jobs[0])
        if job_id is None:
            print("\n⚠ 認不出 job id 欄位，跳過詳情探測。請人工從上面欄位中找出。")
            return 0

        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        detail = await probe_detail(client, job_id)
        if detail is None:
            print("\n⚠ 詳情 endpoint 失敗——工作內容／條件／福利需另尋來源。")
            return 0

        detail_saved = save(detail, out_dir, "detail")
        print(f"\n{'-' * 70}")
        print(f"詳情回應結構（job_id={job_id}）：")
        describe(detail)
        print(f"\n原始回應已存：{detail_saved}")

    return 0


def _guess_job_id(job: dict[str, Any]) -> str | None:
    """從第一筆職缺中認出 job id。欄位名待驗證，故試多個候選。"""
    for key in ("jobNo", "jobId", "job_id", "id"):
        value = job.get(key)
        if value:
            return str(value)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--keyword", default=DEFAULT_KEYWORD, help="職務關鍵字")
    parser.add_argument("--area", default=DEFAULT_AREA, help="104 area code")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs/api-samples"),
        help="原始回應存放目錄",
    )
    args = parser.parse_args()

    return asyncio.run(run(args.keyword, args.area, args.out_dir))


if __name__ == "__main__":
    sys.exit(main())
