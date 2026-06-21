from fastapi import APIRouter

from app.api.routes.feishu_admin_read_tool_im_chat_routes import router as read_tool_im_chat_router
from app.api.routes.feishu_admin_read_tool_im_message_routes import router as read_tool_im_message_router

router = APIRouter()
router.include_router(read_tool_im_chat_router)
router.include_router(read_tool_im_message_router)
