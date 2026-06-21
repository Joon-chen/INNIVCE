from fastapi import APIRouter

from app.api.routes.feishu_admin_api_read_routes import router as admin_api_read_router
from app.api.routes.feishu_admin_app_routes import router as admin_app_router
from app.api.routes.feishu_admin_capability_routes import router as admin_capability_router
from app.api.routes.feishu_admin_read_tool_routes import router as admin_read_tool_router
from app.api.routes.feishu_admin_sync_routes import router as admin_sync_router
from app.api.routes.feishu_admin_write_routes import router as admin_write_router
from app.api.routes.feishu_event_routes import router as event_router
from app.api.routes.feishu_oauth_routes import router as oauth_router

router = APIRouter(prefix="/api/feishu", tags=["feishu"])
router.include_router(admin_api_read_router)
router.include_router(admin_app_router)
router.include_router(admin_capability_router)
router.include_router(admin_read_tool_router)
router.include_router(admin_sync_router)
router.include_router(admin_write_router)
router.include_router(event_router)
router.include_router(oauth_router)
