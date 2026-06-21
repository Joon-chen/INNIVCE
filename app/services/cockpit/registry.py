from collections.abc import Callable

from sqlalchemy.orm import Session

from app.services.cockpit.modules.approvals import build_approvals_module
from app.services.cockpit.modules.communications import build_communications_module
from app.services.cockpit.modules.decisions import build_decisions_module
from app.services.cockpit.modules.meetings import build_meetings_module
from app.services.cockpit.modules.projects import build_projects_module
from app.services.cockpit.modules.reports import build_reports_module
from app.services.cockpit.modules.resources import build_resources_module
from app.services.cockpit.modules.risks import build_risks_module
from app.services.cockpit.modules.tasks import build_tasks_module
from app.services.cockpit.modules.today import build_today_module
from app.services.cockpit.schemas import CockpitModuleResult
from app.services.cockpit.scope import CockpitScope


ModuleBuilder = Callable[[Session, CockpitScope, int], CockpitModuleResult]


MODULE_BUILDERS: dict[str, ModuleBuilder] = {
    "today-focus": build_today_module,
    "risks": build_risks_module,
    "tasks": build_tasks_module,
    "approvals": build_approvals_module,
    "projects": build_projects_module,
    "decisions": build_decisions_module,
    "communications": build_communications_module,
    "meetings": build_meetings_module,
    "resources": build_resources_module,
    "reports": build_reports_module,
}


DEFAULT_MODULE_ORDER = tuple(MODULE_BUILDERS.keys())
