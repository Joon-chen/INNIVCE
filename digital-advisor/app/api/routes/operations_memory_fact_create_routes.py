from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_memory import MemoryFactCreate, create_memory_fact_from_request

router = APIRouter()


@router.post("/memory-facts")
def create_memory_fact(data: MemoryFactCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return create_memory_fact_from_request(db, data)
