"""地區解析測試。

注意：這裡只測「查找邏輯」，不斷言 area code 的實際數值 ——
那些代碼尚未對 104 驗證（見 area_map.py 開頭說明），
現在把值寫死進測試，之後修正代碼時測試會假性失敗。
"""

import pytest

from src.scraper.area_map import (
    DEFAULT_AREA_CODE,
    parse_query,
    resolve_area_code,
)


class TestResolveAreaCode:
    @pytest.mark.parametrize("alias", ["台北", "臺北", "台北市", "臺北市", "北市"])
    def test_台北的各種寫法都能解析(self, alias: str) -> None:
        assert resolve_area_code(alias) == resolve_area_code("臺北市")

    def test_前後空白會被忽略(self) -> None:
        assert resolve_area_code("  台中  ") == resolve_area_code("臺中市")

    def test_無法辨識的地區回傳_None(self) -> None:
        assert resolve_area_code("火星") is None

    def test_空字串回傳_None(self) -> None:
        assert resolve_area_code("") is None
        assert resolve_area_code("   ") is None


class TestParseQuery:
    def test_地區加職務會正確拆開(self) -> None:
        area_code, keyword = parse_query("台北 後端工程師")
        assert area_code == resolve_area_code("臺北市")
        assert keyword == "後端工程師"

    def test_職務含空白時完整保留(self) -> None:
        _, keyword = parse_query("新北 AI 工程師")
        assert keyword == "AI 工程師"

    def test_無法辨識地區時整句當關鍵字並搜全台(self) -> None:
        area_code, keyword = parse_query("資深 Python 工程師")
        assert area_code == DEFAULT_AREA_CODE
        assert keyword == "資深 Python 工程師"

    def test_只給地區沒給職務要報錯(self) -> None:
        with pytest.raises(ValueError, match="職務關鍵字"):
            parse_query("台北")

    def test_空輸入要報錯(self) -> None:
        with pytest.raises(ValueError, match="不可為空"):
            parse_query("   ")
