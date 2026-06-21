from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu import work_event_replies


class EmptyDb:
    def scalars(self, query):
        class Result:
            def all(self):
                return []

        return Result()


def test_approval_instance_status_reads_nested_history_status() -> None:
    event = SimpleNamespace(payload={"item": {"status": "APPROVED"}})

    assert work_event_replies.approval_instance_status(event) == "approved"


def test_format_approval_event_lines_includes_status_title_and_summary() -> None:
    event = SimpleNamespace(
        occurred_at=datetime(2026, 6, 12, 9, 30, tzinfo=UTC),
        payload={"item": {"status": "PENDING"}},
        content_text="  采购付款   需要确认  ",
        title="付款审批",
    )

    lines = work_event_replies.format_approval_event_lines([event])

    assert lines == ["- 2026-06-12 09:30 [pending] 付款审批\n  摘要：采购付款 需要确认"]


def test_safe_command_error_prefers_nested_feishu_message() -> None:
    detail = {"body": {"msg": "permission denied"}, "message": "outer"}

    assert work_event_replies.safe_command_error(detail) == "permission denied"


def test_recent_mail_reply_handles_empty_events() -> None:
    app_config = SimpleNamespace(company_id=uuid4())

    assert work_event_replies.recent_mail_reply(EmptyDb(), app_config) == "还没有同步到邮件。"


def test_open_items_reply_handles_empty_items() -> None:
    app_config = SimpleNamespace(company_id=uuid4())

    assert work_event_replies.open_items_reply(EmptyDb(), app_config) == "当前没有已抽取的开放待办。你可以先发送“同步邮箱”。"
