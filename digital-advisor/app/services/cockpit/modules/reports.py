from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Report
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import scope_filter, short_text


def build_reports_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    query = select(Report).order_by(Report.created_at.desc()).limit(limit)
    query = scope_filter(query, Report, scope)
    reports = list(db.scalars(query).all())
    items = [
        CockpitItem(
            id=str(report.id),
            title=report.title,
            subtitle=report.report_type,
            description=short_text(report.content_markdown, 180),
            occurred_at=report.created_at.isoformat() if report.created_at else None,
            payload={
                "period_start": report.period_start.isoformat() if report.period_start else None,
                "period_end": report.period_end.isoformat() if report.period_end else None,
            },
        )
        for report in reports
    ]
    return CockpitModuleResult(
        key="reports",
        name="报告中心",
        description="日报、周报、月报、风险和决策报告。",
        count=len(reports),
        summary=f"当前已有 {len(reports)} 份报告。" if reports else "当前还没有生成报告。",
        items=items,
        next_actions=[
            "先用日报形成每日经营闭环，再扩展周报和月报。",
            "报告内容应只引用权限允许且已同步入库的数据。",
        ],
        metrics={"reports": len(reports)},
    )
