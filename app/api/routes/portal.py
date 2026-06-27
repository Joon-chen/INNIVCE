from fastapi import APIRouter

from app.api.routes.portal_approval_action_routes import router as approval_action_router
from app.api.routes.portal_approval_detail_routes import router as approval_detail_router
from app.api.routes.portal_bootstrap_routes import router as bootstrap_router
from app.api.routes.portal_cached_approval_routes import router as cached_approval_router
from app.api.routes.portal_index_route import router as index_router
from app.api.routes.portal_pending_approval_routes import router as pending_approval_router
from app.api.routes.portal_result_context_routes import router as result_context_router
from app.api.routes.portal_sidepanel_route import router as sidepanel_router


router = APIRouter(tags=["portal"])
router.include_router(index_router)
router.include_router(sidepanel_router)
router.include_router(pending_approval_router)
router.include_router(cached_approval_router)
router.include_router(result_context_router)
router.include_router(bootstrap_router)
router.include_router(approval_detail_router)
router.include_router(approval_action_router)
