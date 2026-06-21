from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Report, WorkEvent
from app.services.ai.openai_service import AIService
from app.services.audit import write_audit_log


def generate_daily_report(
    db: Session,
    *,
    report_date: date,
    company_id: UUID | None = None,
) -> Report:
    start = datetime.combine(report_date, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    query = select(WorkEvent).where(WorkEvent.occurred_at >= start, WorkEvent.occurred_at < end)
    if company_id:
        query = query.where(WorkEvent.company_id == company_id)
    events = list(db.scalars(query.order_by(WorkEvent.occurred_at.asc())).all())
    event_dicts = [
        {
            "id": str(event.id),
            "source": event.source,
            "event_type": event.event_type,
            "title": event.title,
            "content_text": event.content_text,
            "occurred_at": event.occurred_at.isoformat(),
        }
        for event in events
    ]
    markdown = AIService().daily_report(event_dicts)
    report = Report(
        company_id=company_id,
        report_type="daily",
        period_start=start,
        period_end=end,
        title=f"{report_date.isoformat()} 工作日报",
        content_markdown=markdown,
        payload={"event_count": len(events)},
    )
    db.add(report)
    db.flush()
    write_audit_log(
        db,
        action="report.daily.generated",
        company_id=company_id,
        target_type="report",
        target_id=str(report.id),
        payload={"event_count": len(events)},
    )
    return report
