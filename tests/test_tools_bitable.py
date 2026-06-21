from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.bitable import answer_bitable_question


class _ScalarResult:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class _FakeDb:
    def __init__(self, items):
        self.items = items

    def scalars(self, query):
        return _ScalarResult(self.items)


def test_answer_bitable_question_summarizes_synced_records() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        event_type="feishu.bitable.record",
        title="客户回款表",
        content_text="客户 A 本周回款 120000 元。",
        business_domain="bitable",
        occurred_at=datetime(2026, 6, 12, 11, 0, tzinfo=UTC),
        payload={},
    )

    answer = answer_bitable_question(_FakeDb([event]), company_id=uuid4(), question="多维表格里客户回款怎么样")

    assert "已同步多维表格记录" in answer
    assert "客户回款表" in answer
    assert "120000" in answer


def test_answer_bitable_question_handles_empty_sync() -> None:
    answer = answer_bitable_question(_FakeDb([]), company_id=uuid4(), question="多维表格")

    assert "还没有读到已同步入库的多维表格数据" in answer


def test_answer_bitable_question_shows_master_data_key_fields() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        event_type="feishu.bitable.master_data_index",
        title="多维表格主数据索引：提升用户体验",
        content_text="项目情况概述：项目已完成。",
        business_domain="operations",
        occurred_at=datetime(2026, 6, 12, 11, 0, tzinfo=UTC),
        payload={
            "data_layer": "master_data_index",
            "sync_action": "master_data_index",
            "key_fields": {
                "项目名称": "提升用户体验",
                "状态": "已完成",
                "项目情况概述": "项目已完成，实现了用户满意度提升20%的目标。",
            },
        },
    )

    answer = answer_bitable_question(_FakeDb([event]), company_id=uuid4(), question="项目进展怎么样")

    assert "多维表格主数据索引" in answer
    assert "数据源：PostgreSQL WorkEvent" in answer
    assert "项目名称=提升用户体验" in answer
    assert "状态=已完成" in answer
