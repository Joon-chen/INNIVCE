from fastapi import APIRouter

from app.api.routes.v5_administration_department_routes import router as administration_department_router
from app.api.routes.v5_administration_team_routes import router as administration_team_router

router = APIRouter()

router.include_router(administration_department_router)
router.include_router(administration_team_router)
