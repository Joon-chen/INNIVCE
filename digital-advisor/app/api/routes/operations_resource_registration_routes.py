from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_resources import (
    FeishuResourceRegistrationRequest,
    register_v5_feishu_resource_from_request,
)

router = APIRouter()


@router.post("/feishu/resources")
def register_feishu_resource(data: FeishuResourceRegistrationRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return register_v5_feishu_resource_from_request(db, data)
