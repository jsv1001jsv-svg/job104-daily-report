"""城市名稱 → 104 area code 對照。

⚠️ 尚未驗證：下方 area code 是依 104 搜尋頁常見格式填入的初版，
   **必須**在實作 scraper 時逐一比對 104 網站實際參數後修正。
   驗證方式：到 104 搜尋頁勾選各縣市，觀察網址列的 `area=` 參數。
   驗證進度記錄於 CLAUDE.md 的「開發紀錄」。

本模組只負責「名稱 → 代碼」的查找邏輯，代碼值本身是資料，
兩者分開才能在不動邏輯的前提下修正資料。
"""

# 正式代碼，待驗證（見上方說明）
_CITY_TO_AREA_CODE: dict[str, str] = {
    "臺北市": "6001001000",
    "新北市": "6001002000",
    "基隆市": "6001005000",
    "桃園市": "6001006000",
    "新竹市": "6001007000",
    "新竹縣": "6001008000",
    "苗栗縣": "6001010000",
    "臺中市": "6001011000",
    "彰化縣": "6001014000",
    "南投縣": "6001016000",
    "雲林縣": "6001017000",
    "嘉義市": "6001019000",
    "嘉義縣": "6001018000",
    "臺南市": "6001020000",
    "高雄市": "6001016000",
    "屏東縣": "6001018000",
    "宜蘭縣": "6001004000",
    "花蓮縣": "6001021000",
    "臺東縣": "6001022000",
}

# 使用者實際會打的寫法 → 正式名稱
# 「台」vs「臺」、省略「市/縣」都很常見，不該讓使用者記得打完整名稱
_ALIASES: dict[str, str] = {
    "台北": "臺北市", "臺北": "臺北市", "台北市": "臺北市", "北市": "臺北市",
    "新北": "新北市", "北縣": "新北市",
    "基隆": "基隆市",
    "桃園": "桃園市",
    "新竹": "新竹市",
    "竹科": "新竹市",
    "竹北": "新竹縣",
    "苗栗": "苗栗縣",
    "台中": "臺中市", "臺中": "臺中市", "台中市": "臺中市", "中市": "臺中市",
    "彰化": "彰化縣",
    "南投": "南投縣",
    "雲林": "雲林縣",
    "嘉義": "嘉義市",
    "台南": "臺南市", "臺南": "臺南市", "台南市": "臺南市", "南市": "臺南市",
    "高雄": "高雄市", "高市": "高雄市",
    "屏東": "屏東縣",
    "宜蘭": "宜蘭縣",
    "花蓮": "花蓮縣",
    "台東": "臺東縣", "臺東": "臺東縣",
}

# 使用者沒指定地區時，搜尋全台灣
DEFAULT_AREA_CODE = "6001000000"


def resolve_area_code(city: str) -> str | None:
    """把使用者輸入的地區名轉成 104 area code。

    Args:
        city: 使用者輸入的地區，可為「台北」「臺北市」「北市」等寫法。

    Returns:
        對應的 area code；無法辨識時回傳 None（呼叫端決定要問使用者還是用預設值）。
    """
    normalized = city.strip()
    if not normalized:
        return None

    canonical = _ALIASES.get(normalized, normalized)
    return _CITY_TO_AREA_CODE.get(canonical)


def parse_query(raw_query: str) -> tuple[str, str]:
    """把「台北 後端工程師」拆成 (area_code, keyword)。

    規則：第一個詞若能辨識為地區就當地區，其餘為職務關鍵字；
    辨識不出來就整句當關鍵字、地區用全台灣。

    Args:
        raw_query: 使用者傳給 LINE 官方帳號的原始訊息。

    Returns:
        (area_code, keyword)

    Raises:
        ValueError: raw_query 去除空白後為空字串。
    """
    tokens = raw_query.split()
    if not tokens:
        raise ValueError("搜尋條件不可為空")

    area_code = resolve_area_code(tokens[0])
    if area_code is None:
        return DEFAULT_AREA_CODE, raw_query.strip()

    keyword = " ".join(tokens[1:]).strip()
    if not keyword:
        raise ValueError("只有地區沒有職務關鍵字，請輸入如「台北 後端工程師」")

    return area_code, keyword
