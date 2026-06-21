from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_entities import EntityCreate, create_operations_entity_from_request

router = APIRouter()


@router.post("/entities/{entity_type}")
def create_entity(entity_type: str, data: EntityCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return create_operations_entity_from_request(db, entity_type=entity_type, data=data)
