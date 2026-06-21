"""Shared matrix for complex task routing and task->bitable rewrite guards."""

from __future__ import annotations

from typing import Final


QUERY_TO_BITABLE_ROUTE_HINTS: Final[frozenset[str]] = frozenset(
    {
        "task_qa",
        "feishu_approval_task_query",
        "approval_qa",
        "personal_tasks",
        "mail_qa",
        "chat_tasks",
    }
)

_FORBID_QUERY_TO_BI_COMPLEX_EXECUTION_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"analysis", "decision"}
)


def should_allow_query_to_bitable_route(
    *,
    route_hint: str | None,
    execution_category: str | None,
) -> bool:
    """Whether a route should stay in the query-to-bitable flow.

    A route is allowed by default when either:
    1) it is the primary bitable route
    2) it is in the route-hint allow-list and not currently in a complex category
    """
    if route_hint == "bitable_qa":
        return True
    if route_hint not in QUERY_TO_BITABLE_ROUTE_HINTS:
        return False
    if execution_category in _FORBID_QUERY_TO_BI_COMPLEX_EXECUTION_CATEGORIES:
        return False
    return True
