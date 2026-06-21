from fastapi import APIRouter

from app.api.routes.feishu_admin_read_tool_contact_department_routes import router as contact_department_router
from app.api.routes.feishu_admin_read_tool_contact_snapshot_routes import router as contact_snapshot_router
from app.api.routes.feishu_admin_read_tool_contact_user_routes import router as contact_user_router

router = APIRouter()

router.include_router(contact_department_router)
router.include_router(contact_user_router)
router.include_router(contact_snapshot_router)
