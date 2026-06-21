from fastapi import APIRouter

from app.api.routes.operations_bot_permission_recalculation_routes import (
    router as bot_permission_recalculation_router,
)
from app.api.routes.operations_bot_permission_rule_routes import router as bot_permission_rule_router

router = APIRouter()

router.include_router(bot_permission_rule_router)
router.include_router(bot_permission_recalculation_router)
