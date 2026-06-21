from fastapi import APIRouter

from app.api.routes.feishu_admin_write_approval_routes import router as admin_write_approval_router
from app.api.routes.feishu_admin_write_im_routes import router as admin_write_im_router

router = APIRouter()
router.include_router(admin_write_approval_router)
router.include_router(admin_write_im_router)
