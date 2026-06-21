from typing import Any

from sqlalchemy import and_, false, or_


def memory_fact_access_condition(
    model: Any,
    *,
    principal: Any,
    requested_scope: str,
    chat_id: str | None = None,
) -> Any:
    scope = _normalize_requested_scope(requested_scope)
    if scope == "chat":
        if not chat_id:
            return false()
        own_personal = _own_personal_condition(model, principal)
        chat_memory = and_(model.scope == "chat", model.chat_id == chat_id)
        return or_(chat_memory, own_personal) if own_personal is not None else chat_memory
    if scope == "personal":
        if not getattr(principal, "open_id", None):
            return false()
        return and_(model.scope.in_(("personal", "user")), model.user_open_id == principal.open_id)
    if scope == "domain":
        own_personal = _own_personal_condition(model, principal)
        domain_shared = and_(
            model.scope == "domain",
            or_(model.user_open_id.is_(None), model.user_open_id == getattr(principal, "open_id", None)),
        )
        return or_(domain_shared, own_personal) if own_personal is not None else domain_shared
    if scope == "company" and _can_query_company_memory(principal):
        own_open_id = getattr(principal, "open_id", None)
        return or_(
            model.scope == "company",
            and_(
                model.scope == "domain",
                or_(model.user_open_id.is_(None), model.user_open_id == own_open_id),
            ),
        )
    return false()


def _normalize_requested_scope(scope: str) -> str:
    if scope in {"personal", "self", "user"}:
        return "personal"
    if scope in {"all", "company_management"}:
        return "company"
    if scope in {"group", "conversation"}:
        return "chat"
    return scope


def _own_personal_condition(model: Any, principal: Any) -> Any | None:
    open_id = getattr(principal, "open_id", None)
    if not open_id:
        return None
    return and_(model.scope.in_(("personal", "user")), model.user_open_id == open_id)


def _can_query_company_memory(principal: Any) -> bool:
    return (
        getattr(principal, "role", None) in {"owner", "admin", "ceo"}
        or bool(getattr(principal, "all_companies", False))
        or bool(getattr(principal, "can_query_company", False))
    )
