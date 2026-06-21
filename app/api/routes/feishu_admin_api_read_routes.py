from fastapi import APIRouter

from app.api.routes.feishu_admin_api_read_mail_routes import router as api_read_mail_router

router = APIRouter()
router.include_router(api_read_mail_router)
