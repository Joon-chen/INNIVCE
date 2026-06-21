from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_app_request_models import FeishuAppCreate, FeishuAppOut
from app.db.session import get_db
from app.services.feishu_admin_apps import create_feishu_app_config

router = APIRouter()


@router.post("/apps", response_model=FeishuAppOut, dependencies=[Depends(require_admin_api_token)])
def create_feishu_app(payload: FeishuAppCreate, db: Session = Depends(get_db)) -> Any:
    return create_feishu_app_config(db, payload)
