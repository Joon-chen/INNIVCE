from fastapi import APIRouter

from app.api.routes.user_identity_oauth_feishu_cli_complete_routes import router as feishu_cli_complete_router
from app.api.routes.user_identity_oauth_feishu_cli_start_routes import router as feishu_cli_start_router
from app.api.routes.user_identity_oauth_feishu_start_routes import router as feishu_start_router
from app.api.routes.user_identity_oauth_gmail_callback_routes import router as gmail_callback_router
from app.api.routes.user_identity_oauth_gmail_start_routes import router as gmail_start_router
from app.api.routes.user_identity_oauth_graph_callback_routes import router as graph_callback_router
from app.api.routes.user_identity_oauth_graph_start_routes import router as graph_start_router
from app.api.routes.user_identity_oauth_helpers import _mail_user_identity_oauth_callback

router = APIRouter(prefix="/api/user-identity/oauth", tags=["user-identity-oauth"])

router.include_router(feishu_start_router)
router.include_router(feishu_cli_start_router)
router.include_router(feishu_cli_complete_router)
router.include_router(gmail_start_router)
router.include_router(graph_start_router)
router.include_router(gmail_callback_router)
router.include_router(graph_callback_router)

__all__ = ["_mail_user_identity_oauth_callback", "router"]
