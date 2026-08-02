"""104 職缺 API 的參數組裝與回應解析。

**純函式，不發任何請求。** 送出請求是 transport.py 的事，
這樣「104 的資料長什麼樣」與「怎麼把資料弄到手」可以各自獨立演進 ——
2026-08-02 傳輸層從 Playwright 換成 curl_cffi 時，本模組一行都不用改。

驗證狀態（2026-08-01 對真實回應驗證，2026-08-02 再次確認）
----------------------------------------------------------
搜尋：`GET /jobs/search/api/jobs?area=&jobcat=&page=&pagesize=`
      回應的 `data` **直接是陣列**（不是 data.list），分頁在 metadata.pagination。

詳情：`GET /api/jobs/{slug}`（不是舊教學寫的 /job/ajax/content/{id}）
      只有這支有「條件」與「福利」，是日報必要欄位。

⚠️ 兩套 ID 別搞混：搜尋結果的 `jobNo`（14950369）是數字流水號，
   詳情 API 只吃**網址短碼**（8wfs1，藏在 link.job 裡）。本模組統一用短碼。
"""

import logging

from src.models import Job

logger = logging.getLogger(__name__)

_JOB_DETAIL_URL = "https://www.104.com.tw/api/jobs/{job_id}"
_JOB_PAGE_URL = "https://www.104.com.tw/job/{job_id}"

# 104 單頁上限。要更多筆得用 page 參數分頁，分頁資訊在 metadata.pagination
_MAX_PAGE_SIZE = 20

# 104 用這個值表示薪資「以上」，不是真的上限
_SALARY_UNBOUNDED = 9_999_999


class ScraperError(RuntimeError):
    """抓取失敗。呼叫端應記錄並跳過該使用者，不要讓整批日報中斷。"""


def build_search_params(
    keyword: str,
    area_code: str,
    limit: int,
    jobcat: str | None = None,
) -> dict[str, str]:
    """組搜尋參數。

    抽成獨立函式是為了讓 httpx 與瀏覽器兩種傳輸方式共用同一份定義 ——
    104 的搜尋「頁面」與搜尋「API」吃的參數同名，瀏覽器層把這組參數
    掛在頁面網址上，頁面就會用同樣條件去打 API。
    """
    params = {
        "area": area_code,
        "order": "11",          # 依更新日期排序，新職缺優先
        "asc": "0",
        "page": "1",
        "pagesize": str(min(limit, _MAX_PAGE_SIZE)),
        "jobsource": "m_joblist_search",
    }
    if jobcat:
        params["jobcat"] = jobcat
    else:
        params["keyword"] = keyword
    return params


def parse_search_payload(payload: dict, limit: int) -> list[Job]:
    """把搜尋 API 的原始回應轉成 Job 清單。

    與傳輸方式無關 —— httpx 或瀏覽器拿到的 JSON 都走這裡，
    確保兩條路徑的解析結果一致。
    """
    raw_jobs = _extract_job_list(payload)
    return [_search_item_to_job(raw) for raw in raw_jobs[:limit]]


def parse_detail_payload(job_id: str, payload: dict) -> Job:
    """把詳情 API 的原始回應轉成 Job。

    Raises:
        ScraperError: 回應缺少 data 物件。
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ScraperError(f"詳情回應缺少 data 物件（job_id={job_id}）：{list(payload)}")

    return _detail_to_job(job_id, data)


def job_page_url(job_id: str) -> str:
    """職缺頁網址。詳情 API 需要用它當 Referer。"""
    return _JOB_PAGE_URL.format(job_id=job_id)


def job_detail_url(job_id: str) -> str:
    """職缺詳情 API 網址。"""
    return _JOB_DETAIL_URL.format(job_id=job_id)


def _extract_job_list(payload: dict) -> list[dict]:
    """從 104 回應中取出職缺陣列。

    payload["data"] **直接就是陣列**，不是物件——舊教學寫的 data.list 是錯的。
    分頁資訊在 payload["metadata"]["pagination"]。
    """
    data = payload.get("data")
    if not isinstance(data, list):
        raise ScraperError(f"回應 data 不是陣列（實際為 {type(data).__name__}）：{list(payload)}")

    return data


def _search_item_to_job(raw: dict) -> Job:
    """把搜尋結果的一筆轉成 Job。

    欄位路徑於 2026-08-01 對真實回應驗證。搜尋列表沒有條件與福利，
    這兩欄留空，由 fetch_job_detail() 補齊。
    """
    job_url = _normalize_url(_get_job_link(raw))

    return Job(
        job_id=_extract_job_slug(job_url),
        title=raw.get("jobName", ""),
        company=raw.get("custName", ""),
        url=job_url,
        location=raw.get("jobAddrNoDesc", ""),
        salary=_format_salary(raw),
        description=raw.get("description", ""),
        requirement="",
        benefit="",
    )


def _get_job_link(raw: dict) -> str:
    """取出職缺網址。104 放在巢狀的 link.job。"""
    link = raw.get("link")
    return link.get("job", "") if isinstance(link, dict) else ""


def _extract_job_slug(job_url: str) -> str:
    """從職缺網址取出短碼，例：.../job/8wfs1 → 8wfs1。

    104 有兩套 ID：
      - `jobNo`（如 14950369）：搜尋結果用的數字流水號
      - **短碼**（如 8wfs1）：網址與詳情 API 用的

    詳情 API 只吃短碼，所以整個專案統一用短碼當 job_id，
    Firestore 的 seen_jobs 也才不會跟詳情查詢對不起來。
    """
    return job_url.rstrip("/").rsplit("/", 1)[-1]


def _format_salary(raw: dict) -> str:
    """把薪資上下限組成可讀字串。

    搜尋列表沒有「待遇面議」這種文字欄位，只有數字上下限，
    兩者皆為 0 代表面議；上限 9999999 是 104 表示「以上」的哨兵值。
    """
    low = raw.get("salaryLow") or 0
    high = raw.get("salaryHigh") or 0

    if not low and not high:
        return "待遇面議"
    if high >= _SALARY_UNBOUNDED or not high:
        return f"{low:,} 以上"
    if not low:
        return f"{high:,} 以下"
    if low == high:
        return f"{low:,}"
    return f"{low:,} ~ {high:,}"


def _normalize_url(link: str) -> str:
    """104 回傳的連結常是 //www.104.com.tw/job/xxx 形式，補上 scheme。"""
    if link.startswith("//"):
        return f"https:{link}"
    return link


def _detail_to_job(job_id: str, data: dict) -> Job:
    """把詳情 API 的 data 物件轉成領域模型。

    欄位路徑於 2026-08-01 對真實回應驗證，取樣見 docs/api-samples/。
    集中在這一個函式，104 改版時只需改這裡。
    """
    header = data.get("header") or {}
    detail = data.get("jobDetail") or {}

    return Job(
        job_id=job_id,
        title=header.get("jobName", ""),
        company=header.get("custName", ""),
        url=_JOB_PAGE_URL.format(job_id=job_id),
        location=detail.get("addressRegion") or detail.get("addressArea", ""),
        salary=detail.get("salary", ""),
        description=detail.get("jobDescription", ""),
        requirement=_format_condition(data.get("condition") or {}),
        benefit=_format_welfare(data.get("welfare") or {}),
    )


def _format_condition(condition: dict) -> str:
    """把散落的應徵條件欄位併成一段文字，供 LLM 摘要。

    104 把條件拆成經歷、學歷、專長、技能、其他等欄位，
    這裡保留標籤是為了讓 LLM 知道每段的語意，摘要品質較穩。
    """
    parts: list[str] = []

    if work_exp := condition.get("workExp"):
        parts.append(f"工作經驗：{work_exp}")
    if edu := condition.get("edu"):
        parts.append(f"學歷：{edu}")

    if specialties := _describe_all(condition.get("specialty")):
        parts.append(f"專長：{specialties}")
    if skills := _describe_all(condition.get("skill")):
        parts.append(f"技能：{skills}")

    if other := condition.get("other"):
        parts.append(f"其他條件：{other}")

    return "\n".join(parts)


def _format_welfare(welfare: dict) -> str:
    """把福利標籤與說明併成一段文字。"""
    parts: list[str] = []

    tags = welfare.get("tag")
    if isinstance(tags, list) and tags:
        parts.append("、".join(str(tag) for tag in tags))

    if description := welfare.get("welfare"):
        parts.append(str(description))

    return "\n".join(parts)


def _describe_all(items: object) -> str:
    """104 常用 [{code, description}, ...] 結構，取出 description 併成一行。"""
    if not isinstance(items, list):
        return ""
    return "、".join(
        str(item["description"])
        for item in items
        if isinstance(item, dict) and item.get("description")
    )
