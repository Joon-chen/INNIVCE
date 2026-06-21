from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class DailyReportRequest(BaseModel):
    report_date: date
    company_id: UUID | None = None


class VectorSearchRequest(BaseModel):
    company_id: UUID
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    chat_id: str | None = None


class VectorizePendingRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=1000)
