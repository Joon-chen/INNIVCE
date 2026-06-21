from uuid import uuid4

from app.api.routes.v5 import router as v5_router
from app.api.routes.v5_capability_registry_routes import capability_registry


class DummyResponse:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def test_capability_registry_route_is_registered() -> None:
    paths = {route.path for route in v5_router.routes}

    assert "/api/v5/capability-registry" in paths


def test_capability_registry_read_api_contract() -> None:
    response = DummyResponse()
    company_id = uuid4()

    payload = capability_registry(company_id=company_id, response=response, db=None)  # type: ignore[arg-type]

    assert response.headers["Cache-Control"] == "private, max-age=30"
    assert payload["registry_version"] == "capability_registry_v1"
    assert payload["generated_at"]
    assert payload["company_id"] == str(company_id)
    assert set(payload) >= {
        "catalog_payload",
        "skill_registry_payload",
        "governance_payload",
        "diagnostics_payload",
        "registry_health",
    }
    assert payload["catalog_payload"]["domains"]
    assert payload["skill_registry_payload"]["capabilities"]
    assert "findings" in payload["governance_payload"]
    assert "summary" in payload["diagnostics_payload"]
    assert set(payload["registry_health"]) >= {
        "status",
        "domains",
        "capabilities",
        "skills",
        "providers",
        "findings",
        "orphan_skills",
        "missing_provider",
    }
