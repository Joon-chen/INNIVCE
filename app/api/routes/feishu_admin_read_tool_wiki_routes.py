from fastapi import APIRouter

from app.api.routes.feishu_admin_read_tool_wiki_node_routes import router as read_tool_wiki_node_router
from app.api.routes.feishu_admin_read_tool_wiki_space_routes import router as read_tool_wiki_space_router

router = APIRouter()
router.include_router(read_tool_wiki_space_router)
router.include_router(read_tool_wiki_node_router)
