from fastapi import APIRouter

from app.api.routes.feishu_admin_write_im_auto_join_routes import router as im_auto_join_router
from app.api.routes.feishu_admin_write_im_chat_routes import router as im_chat_router
from app.api.routes.feishu_admin_write_im_message_routes import router as im_message_router

router = APIRouter()

router.include_router(im_message_router)
router.include_router(im_chat_router)
router.include_router(im_auto_join_router)
