from __future__ import annotations

from typing import Any

from app.services.runtime_v5.models import CommandPlan, PermissionDecision, ResultContext


FILTER_VERSION = "policy_result_filter_v0"
COGNITIVE_RESOURCE_TYPES = {"workevent", "evidence", "snapshot", "insight", "memory"}
AGGREGATE_SCOPES = {"TEAM", "DEPARTMENT", "COMPANY"}


def build_policy_result_filter_payload(
    *,
    command_plan: CommandPlan,
    permission: PermissionDecision,
    result_context: ResultContext | None,
    scope_context: dict[str, Any],
) -> dict[str, Any]:
    """Build the Policy Result Filter decision skeleton for RuntimeResult.

    V0 records the decision contract without mutating result content. Real
    redaction can be attached here later while keeping Card/Portal render-only.
    """

    policy_subject = _dict_metadata(permission, "policy_subject")
    policy_scope = _dict_metadata(permission, "policy_scope")
    identity_decision = _dict_metadata(permission, "identity_decision")
    allowed_resource_types = _list_metadata(permission, "allowed_resource_types", fallback=command_plan.planner_result.sources)
    denied_resource_types = _list_metadata(permission, "denied_resource_types")
    scope = str(scope_context.get("scope") or "").upper()
    denied_resource_type_set = {resource_type.lower() for resource_type in denied_resource_types}
    resource_filters = tuple(
        _resource_filter(
            index=index,
            item=item,
            allowed=permission.allowed,
            scope=scope,
            denied_resource_types=denied_resource_type_set,
            policy_subject=policy_subject,
        )
        for index, item in enumerate(result_context.items if result_context is not None else ())
    )
    section_filters = {
        resource_type: _section_filter(
            resource_type=resource_type,
            allowed=permission.allowed,
            scope=scope,
            denied_resource_types=denied_resource_type_set,
        )
        for resource_type in _ordered_resource_types(allowed_resource_types, denied_resource_types)
    }
    return {
        "status": "applied",
        "filter_version": FILTER_VERSION,
        "scope": scope or "SELF",
        "policy_subject": policy_subject,
        "policy_scope": policy_scope,
        "identity_decision": identity_decision,
        "allowed_resource_types": allowed_resource_types,
        "denied_resource_types": denied_resource_types,
        "resource_filters": list(resource_filters),
        "section_filters": section_filters,
        "redaction_applied": False,
        "aggregation_only": any(decision["aggregation_only"] for decision in section_filters.values())
        or any(decision["aggregation_only"] for decision in resource_filters),
        "source_reference_visible": all(decision["source_reference_visible"] for decision in section_filters.values())
        and all(decision["source_reference_visible"] for decision in resource_filters),
    }


def apply_policy_result_filter(
    *,
    items: tuple[dict[str, Any], ...],
    filter_payload: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Apply the V0 result filter to item copies before Interaction rendering."""

    decisions = filter_payload.get("resource_filters") if isinstance(filter_payload.get("resource_filters"), list) else []
    by_index = {decision.get("index"): decision for decision in decisions if isinstance(decision, dict)}
    filtered: list[dict[str, Any]] = []
    redaction_applied = False
    for index, item in enumerate(items):
        decision = by_index.get(index)
        if not isinstance(decision, dict):
            filtered.append(dict(item))
            continue
        if not decision.get("visible", True):
            redaction_applied = True
            continue
        redacted = _redacted_item_copy(item, decision=decision)
        redaction_applied = redaction_applied or redacted != item
        filtered.append(redacted)
    if redaction_applied:
        filter_payload = {**filter_payload, "redaction_applied": True}
    return tuple(filtered), filter_payload


def _resource_filter(
    *,
    index: int,
    item: dict[str, Any],
    allowed: bool,
    scope: str,
    denied_resource_types: set[str],
    policy_subject: dict[str, Any],
) -> dict[str, Any]:
    resource_type = _resource_type(item)
    visible = allowed and resource_type not in denied_resource_types and _resource_visible_to_subject(item=item, policy_subject=policy_subject)
    cognitive = _is_cognitive_resource(item=item, resource_type=resource_type)
    aggregate = visible and cognitive and scope in AGGREGATE_SCOPES
    return {
        "index": index,
        "resource_type": resource_type,
        "visible": visible,
        "redacted_fields": _source_reference_fields(item) if aggregate else [],
        "hidden_sections": [],
        "aggregation_only": aggregate,
        "reason_hidden": aggregate,
        "source_reference_visible": visible and not aggregate,
    }


def _resource_visible_to_subject(*, item: dict[str, Any], policy_subject: dict[str, Any]) -> bool:
    if not _has_visibility_markers(item):
        return True
    visibility_scope = _visibility_scope(item)
    if visibility_scope in {"", "PUBLIC", "COMPANY", "ORGANIZATION"}:
        return True
    actor_ids = _subject_actor_ids(policy_subject)
    if visibility_scope in {"SELF", "OWNER", "PRIVATE", "PERSONAL"}:
        allowed_user_ids = _item_string_list(item, "allowed_user_ids")
        if allowed_user_ids and actor_ids & set(allowed_user_ids):
            return True
        owner_ids = {value for value in (_item_string(item, "owner_user_id"), _item_string(item, "owner_open_id")) if value}
        return bool(actor_ids and owner_ids and actor_ids & owner_ids)
    if visibility_scope in {"DEPARTMENT", "TEAM", "GROUP"}:
        subject_department_ids = _subject_department_ids(policy_subject)
        allowed_departments = set(_item_string_list(item, "allowed_department_ids") or _item_string_list(item, "allowed_departments"))
        owner_department_id = _item_string(item, "owner_department_id")
        item_departments = {owner_department_id, *allowed_departments}
        item_departments = {department_id for department_id in item_departments if department_id}
        return bool(subject_department_ids and item_departments and subject_department_ids & item_departments)
    return False


def _has_visibility_markers(item: dict[str, Any]) -> bool:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    markers = (
        "visibility_scope",
        "inherited_visibility_scope",
        "allowed_user_ids",
        "allowed_department_ids",
        "allowed_departments",
        "owner_user_id",
        "owner_open_id",
        "owner_department_id",
    )
    return any(key in item or key in raw for key in markers)


def _visibility_scope(item: dict[str, Any]) -> str:
    return str(_item_string(item, "visibility_scope") or _item_string(item, "inherited_visibility_scope")).strip().upper()


def _subject_actor_ids(policy_subject: dict[str, Any]) -> set[str]:
    return {
        value
        for value in (
            str(policy_subject.get("actor_user_id") or "").strip(),
            str(policy_subject.get("actor_open_id") or "").strip(),
        )
        if value
    }


def _subject_department_ids(policy_subject: dict[str, Any]) -> set[str]:
    department_ids = set(_string_list(policy_subject.get("departments")))
    department_ids.update(_string_list(policy_subject.get("department_ids")))
    for item in policy_subject.get("managed_departments") or ():
        if isinstance(item, dict):
            department_id = str(item.get("id") or item.get("department_id") or "").strip()
            if department_id:
                department_ids.add(department_id)
    for item in policy_subject.get("management_scope") or ():
        if isinstance(item, dict):
            department_id = str(item.get("department_id") or item.get("id") or "").strip()
            if department_id:
                department_ids.add(department_id)
    return department_ids


def _item_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if value is None and isinstance(item.get("raw"), dict):
        value = item["raw"].get(key)
    return str(value or "").strip()


def _item_string_list(item: dict[str, Any], key: str) -> list[str]:
    value = item.get(key)
    if value is None and isinstance(item.get("raw"), dict):
        value = item["raw"].get(key)
    return _string_list(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _section_filter(*, resource_type: str, allowed: bool, scope: str, denied_resource_types: set[str]) -> dict[str, Any]:
    visible = allowed and resource_type not in denied_resource_types
    cognitive = resource_type in COGNITIVE_RESOURCE_TYPES
    aggregate = visible and cognitive and scope in AGGREGATE_SCOPES
    return {
        "visible": visible,
        "redacted_fields": [],
        "hidden_sections": [],
        "aggregation_only": aggregate,
        "reason_hidden": aggregate,
        "source_reference_visible": visible and not aggregate,
    }


def _resource_type(item: dict[str, Any]) -> str:
    for key in ("resource_type", "source", "object_type", "type"):
        value = str(item.get(key) or "").strip().lower()
        if value:
            return value
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    for key in ("resource_type", "source", "object_type", "type"):
        value = str(raw.get(key) or "").strip().lower()
        if value:
            return value
    return "operational"


def _is_cognitive_resource(*, item: dict[str, Any], resource_type: str) -> bool:
    if str(item.get("resource_plane") or "").strip().lower() == "cognitive":
        return True
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    if str(raw.get("resource_plane") or "").strip().lower() == "cognitive":
        return True
    return resource_type in COGNITIVE_RESOURCE_TYPES


def _redacted_item_copy(item: dict[str, Any], *, decision: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(item)
    for field in decision.get("redacted_fields") or []:
        redacted.pop(str(field), None)
    if isinstance(redacted.get("raw"), dict):
        raw = dict(redacted["raw"])
        for field in decision.get("redacted_fields") or []:
            raw.pop(str(field), None)
        redacted["raw"] = raw
    return redacted


def _source_reference_fields(item: dict[str, Any]) -> list[str]:
    candidate_fields = (
        "source_event_id",
        "source_event_ids",
        "evidence_event_ids",
        "memory_reference_ids",
        "source_reference",
        "source_references",
        "source_object_id",
        "source_object_type",
        "source_system",
        "inherited_visibility_scope",
        "raw",
    )
    fields = [field for field in candidate_fields if field in item]
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    fields.extend(field for field in candidate_fields if field in raw and field not in fields)
    return fields


def _dict_metadata(permission: PermissionDecision, key: str) -> dict[str, Any]:
    value = permission.metadata.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _list_metadata(permission: PermissionDecision, key: str, *, fallback: tuple[str, ...] = ()) -> list[str]:
    value = permission.metadata.get(key)
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(item) for item in fallback if str(item).strip()]


def _ordered_resource_types(*resource_type_groups: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in resource_type_groups:
        for resource_type in group:
            normalized = str(resource_type).strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
    return ordered
