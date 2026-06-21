from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.ai.business_extraction import enrich_open_business_items
from app.services.ai.risk_noise import close_finance_document_noise_risks, close_low_signal_extraction_noise


def close_finance_document_risk_noise_items(
    db: Session,
    *,
    company_id: UUID | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    result = close_finance_document_noise_risks(db, company_id=company_id, limit=_bounded_intelligence_limit(limit))
    db.commit()
    return result


def close_low_signal_notice_noise_items(
    db: Session,
    *,
    company_id: UUID | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    result = close_low_signal_extraction_noise(db, company_id=company_id, limit=_bounded_intelligence_limit(limit))
    db.commit()
    return result


def enrich_open_business_extracted_items(
    db: Session,
    *,
    company_id: UUID | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    result = enrich_open_business_items(db, company_id=company_id, limit=_bounded_intelligence_limit(limit))
    db.commit()
    return result


def _bounded_intelligence_limit(limit: int) -> int:
    return min(max(limit, 1), 2000)
