"""職缺詳情解析測試。

fixture 取自 2026-08-01 對 https://www.104.com.tw/api/jobs/8wfs1 的真實回應
（已裁剪掉聯絡人 email、環境照片等與解析無關的欄位）。
這些是「照著真實格式」的測試，不是猜測 —— 104 改欄位時應該由這裡先爆。
"""

import pytest

from src.scraper.client import _detail_to_job, _format_condition, _format_welfare


@pytest.fixture
def detail_data() -> dict:
    """真實詳情回應的 data 物件。"""
    return {
        "header": {
            "jobName": "Java工程師",
            "custName": "昇智科技有限公司",
            "appearDate": "2026/08/01",
        },
        "condition": {
            "workExp": "4年以上",
            "edu": "大學",
            "specialty": [
                {"code": "12001002005", "description": "OOP"},
                {"code": "12001003025", "description": "Java"},
            ],
            "skill": [
                {"code": "11009005001", "description": "軟體程式設計"},
            ],
            "other": "1. 熟悉Java、springboot。",
        },
        "welfare": {
            "tag": ["零食櫃", "咖啡吧", "生育津貼"],
            "welfare": "三節獎金\n績效獎金",
        },
        "jobDetail": {
            "jobDescription": "1. 具備Java 實務開發、測試與系統維護經驗。",
            "salary": "待遇面議",
            "addressRegion": "台北市松山區",
            "addressArea": "台北市",
            "addressDetail": "光復南路1號3樓",
        },
    }


class TestDetailToJob:
    def test_基本欄位對應正確(self, detail_data: dict) -> None:
        job = _detail_to_job("8wfs1", detail_data)

        assert job.job_id == "8wfs1"
        assert job.title == "Java工程師"
        assert job.company == "昇智科技有限公司"
        assert job.salary == "待遇面議"
        assert job.description == "1. 具備Java 實務開發、測試與系統維護經驗。"

    def test_網址由_job_id_組出(self, detail_data: dict) -> None:
        job = _detail_to_job("8wfs1", detail_data)
        assert job.url == "https://www.104.com.tw/job/8wfs1"

    def test_地點優先用含行政區的欄位(self, detail_data: dict) -> None:
        job = _detail_to_job("8wfs1", detail_data)
        assert job.location == "台北市松山區"

    def test_缺_addressRegion_時退回_addressArea(self, detail_data: dict) -> None:
        del detail_data["jobDetail"]["addressRegion"]
        job = _detail_to_job("8wfs1", detail_data)
        assert job.location == "台北市"

    def test_條件與福利都有填入(self, detail_data: dict) -> None:
        job = _detail_to_job("8wfs1", detail_data)
        assert "4年以上" in job.requirement
        assert "零食櫃" in job.benefit

    def test_尚未經過摘要(self, detail_data: dict) -> None:
        job = _detail_to_job("8wfs1", detail_data)
        assert job.is_summarized is False

    @pytest.mark.parametrize("missing", ["header", "condition", "welfare", "jobDetail"])
    def test_缺任一區塊也不炸(self, detail_data: dict, missing: str) -> None:
        """104 偶爾會回傳 null 區塊，不該讓整批日報中斷。"""
        detail_data[missing] = None
        job = _detail_to_job("8wfs1", detail_data)
        assert job.job_id == "8wfs1"


class TestFormatCondition:
    def test_保留欄位標籤讓_LLM_知道語意(self, detail_data: dict) -> None:
        text = _format_condition(detail_data["condition"])

        assert "工作經驗：4年以上" in text
        assert "學歷：大學" in text
        assert "專長：OOP、Java" in text
        assert "技能：軟體程式設計" in text
        assert "其他條件：" in text

    def test_空條件回傳空字串(self) -> None:
        assert _format_condition({}) == ""

    def test_跳過空的清單欄位(self) -> None:
        text = _format_condition({"edu": "大學", "specialty": [], "skill": None})
        assert text == "學歷：大學"

    def test_忽略沒有_description_的項目(self) -> None:
        text = _format_condition({"specialty": [{"code": "x"}, {"description": "Java"}]})
        assert text == "專長：Java"


class TestFormatWelfare:
    def test_標籤與說明都保留(self, detail_data: dict) -> None:
        text = _format_welfare(detail_data["welfare"])

        assert "零食櫃、咖啡吧、生育津貼" in text
        assert "三節獎金" in text

    def test_空福利回傳空字串(self) -> None:
        assert _format_welfare({}) == ""

    def test_只有標籤沒有說明(self) -> None:
        assert _format_welfare({"tag": ["零食櫃"]}) == "零食櫃"
