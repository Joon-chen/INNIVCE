from fastapi import APIRouter

from app.api.routes.feishu_admin_read_tool_mail_folder_routes import router as read_tool_mail_folder_router
from app.api.routes.feishu_admin_read_tool_mail_message_detail_routes import (
    router as read_tool_mail_message_detail_router,
)
from app.api.routes.feishu_admin_read_tool_mail_message_list_routes import (
    router as read_tool_mail_message_list_router,
)

router = APIRouter()
router.include_router(read_tool_mail_folder_router)
router.include_router(read_tool_mail_message_detail_router)
router.include_router(read_tool_mail_message_list_router)
