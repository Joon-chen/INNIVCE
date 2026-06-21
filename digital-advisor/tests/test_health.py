from app.api.routes.health import health
from app.main import app


def test_health_route_is_registered_for_release_checks() -> None:
    assert "/health" in {route.path for route in app.routes}
    assert health() == {"status": "ok"}
