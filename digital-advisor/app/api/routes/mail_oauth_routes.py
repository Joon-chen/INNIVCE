from fastapi import APIRouter

from app.api.routes.mail_oauth_gmail_routes import router as mail_oauth_gmail_router
from app.api.routes.mail_oauth_graph_routes import router as mail_oauth_graph_router

router = APIRouter()

router.include_router(mail_oauth_gmail_router)
router.include_router(mail_oauth_graph_router)
