from datetime import UTC, datetime

from app.db.session import SessionLocal
from app.models.entities import Company
from app.schemas.common import WorkEventCreate
from app.services.ai.reports import generate_daily_report
from app.services.work_events import upsert_work_event


def main() -> None:
    db = SessionLocal()
    try:
        company = Company(name="Demo Company", code="demo")
        db.add(company)
        db.flush()
        upsert_work_event(
            db,
            WorkEventCreate(
                company_id=company.id,
                source="demo",
                event_type="mail.message",
                external_id="demo-001",
                title="确认下周项目排期",
                content_text="决定下周完成接口联调。风险：审批流程可能延期。待办：张三跟进飞书权限。",
                occurred_at=datetime.now(UTC),
                labels=["demo"],
            ),
        )
        report = generate_daily_report(db, report_date=datetime.now(UTC).date(), company_id=company.id)
        db.commit()
        print(f"company_id={company.id}")
        print(f"report_id={report.id}")
        print(report.content_markdown)
    finally:
        db.close()


if __name__ == "__main__":
    main()
