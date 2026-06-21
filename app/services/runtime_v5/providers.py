from __future__ import annotations

from app.services.runtime_v5.models import ProviderRequest, ProviderResult


class NotImplementedProvider:
    def __init__(self, source: str) -> None:
        self.source = source

    def execute(self, request: ProviderRequest) -> ProviderResult:
        return ProviderResult(
            source=self.source,
            status="skipped",
            error=f"provider_not_implemented:{request.source}.{request.operation}",
        )


def default_provider_registry() -> dict[str, NotImplementedProvider]:
    sources = (
        "people",
        "approval",
        "calendar",
        "task",
        "mail",
        "im",
        "docs",
        "wiki",
        "drive",
        "sheets",
        "vc",
        "minutes",
        "attendance",
        "okr",
        "slides",
        "whiteboard",
        "vc_agent",
        "base",
        "company_profile",
        "knowledge",
        "workevent",
        "memory",
        "web",
    )
    return {source: NotImplementedProvider(source) for source in sources}
