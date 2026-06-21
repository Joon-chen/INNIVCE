from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin_api_token
from app.api.routes.mail_graph_routes import router as mail_graph_router
from app.api.routes.mail_imap_routes import router as mail_imap_router
from app.api.routes.mail_oauth_routes import router as mail_oauth_router

router = APIRouter(prefix="/api/mail", tags=["mail"], dependencies=[Depends(require_admin_api_token)])
router.include_router(mail_graph_router)
router.include_router(mail_imap_router)
router.include_router(mail_oauth_router)
