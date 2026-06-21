from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin_api_token
from app.api.routes.operations_advisor_routes import router as advisor_router
from app.api.routes.operations_audit_routes import router as audit_router
from app.api.routes.operations_automation_status_routes import router as automation_status_router
from app.api.routes.operations_bot_permission_routes import router as bot_permission_router
from app.api.routes.operations_bot_user_list_routes import router as bot_user_list_router
from app.api.routes.operations_bot_user_upsert_routes import router as bot_user_upsert_router
from app.api.routes.operations_dashboard_routes import router as dashboard_router
from app.api.routes.operations_entity_routes import router as entity_router
from app.api.routes.operations_extracted_routes import router as extracted_router
from app.api.routes.operations_memory_fact_create_routes import router as memory_fact_create_router
from app.api.routes.operations_memory_fact_list_routes import router as memory_fact_list_router
from app.api.routes.operations_memory_generation_routes import router as memory_generation_router
from app.api.routes.operations_report_routes import router as report_router
from app.api.routes.operations_resource_list_routes import router as resource_list_router
from app.api.routes.operations_resource_registration_routes import router as resource_registration_router
from app.api.routes.operations_system_status_routes import router as system_status_router
from app.api.routes.operations_sync_routes import router as sync_router

router = APIRouter(prefix="/api", tags=["operations"], dependencies=[Depends(require_admin_api_token)])
router.include_router(dashboard_router)
router.include_router(advisor_router)
router.include_router(system_status_router)
router.include_router(automation_status_router)
router.include_router(sync_router)
router.include_router(extracted_router)
router.include_router(report_router)
router.include_router(audit_router)
router.include_router(bot_user_upsert_router)
router.include_router(bot_user_list_router)
router.include_router(bot_permission_router)
router.include_router(resource_registration_router)
router.include_router(resource_list_router)
router.include_router(memory_fact_create_router)
router.include_router(memory_fact_list_router)
router.include_router(memory_generation_router)
router.include_router(entity_router)
