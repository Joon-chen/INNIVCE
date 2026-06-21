from fastapi import APIRouter

from app.api.routes.feishu_admin_read_tool_bitable_record_routes import router as bitable_record_router
from app.api.routes.feishu_admin_read_tool_bitable_table_routes import router as bitable_table_router

router = APIRouter()

router.include_router(bitable_table_router)
router.include_router(bitable_record_router)
