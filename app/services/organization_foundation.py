from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    OrganizationAlias,
    OrganizationDepartment,
    OrganizationMembership,
    OrganizationSyncRun,
    OrganizationUser,
)


ORG_TARGET_DEPARTMENT = "department"
ORG_TARGET_USER = "user"
ORG_TARGET_GROUP = "group"


@dataclass(frozen=True)
class OrganizationCandidate:
    target_type: str
    target_id: str
    name: str
    confidence: float
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrganizationResolution:
    query: str
    normalized_query: str
    resolved_type: str = ""
    resolved_id: str = ""
    resolved_name: str = ""
    confidence: float = 0.0
    candidates: tuple[OrganizationCandidate, ...] = ()
    reason: str = ""
    needs_clarification: bool = False

    @property
    def resolved_department_id(self) -> str:
        return self.resolved_id if self.resolved_type in {ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP} else ""

    @property
    def resolved_user_id(self) -> str:
        return self.resolved_id if self.resolved_type == ORG_TARGET_USER else ""


@dataclass(frozen=True)
class OrganizationDirectory:
    departments: tuple[dict[str, Any], ...] = ()
    users: tuple[dict[str, Any], ...] = ()
    aliases: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class OrganizationMembersResult:
    resolution: OrganizationResolution
    items: tuple[dict[str, Any], ...]
    source: str = "organization_foundation"


def normalize_organization_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s，,。.!！；;：:？?（）()\[\]【】]+", "", text)
    for token in (
        "公司",
        "集团",
        "请问",
        "帮我查",
        "查一下",
        "有多少人",
        "多少人",
        "都有谁",
        "分别是谁",
        "是谁",
        "谁是",
        "分别",
        "有哪些人",
        "成员",
        "人员",
        "同事",
        "名单",
        "的",
    ):
        text = text.replace(token, "")
    return text.strip()


def organization_directory_from_payload(payload: dict[str, Any]) -> OrganizationDirectory:
    departments = payload.get("departments") if isinstance(payload.get("departments"), list) else []
    users = payload.get("users") if isinstance(payload.get("users"), list) else []
    return OrganizationDirectory(
        departments=tuple(_department_record(item) for item in departments if isinstance(item, dict)),
        users=tuple(_user_record(item) for item in users if isinstance(item, dict)),
        aliases=(),
    )


def load_organization_directory(db: Session, *, company_id: UUID) -> OrganizationDirectory:
    departments = db.scalars(
        select(OrganizationDepartment)
        .where(OrganizationDepartment.company_id == company_id)
        .where(OrganizationDepartment.status == "active")
    ).all()
    users = db.scalars(
        select(OrganizationUser)
        .where(OrganizationUser.company_id == company_id)
        .where(OrganizationUser.status == "active")
    ).all()
    aliases = db.scalars(
        select(OrganizationAlias)
        .where(OrganizationAlias.company_id == company_id)
        .where(OrganizationAlias.is_active.is_(True))
    ).all()
    return OrganizationDirectory(
        departments=tuple(_department_model_record(item) for item in departments),
        users=tuple(_user_model_record(item) for item in users),
        aliases=tuple(_alias_model_record(item) for item in aliases),
    )


def resolve_policy_subject_from_organization(
    db: Session,
    *,
    company_id: UUID,
    open_id: str,
) -> dict[str, Any]:
    open_id = str(open_id or "").strip()
    if not open_id:
        return {}
    user = db.scalar(
        select(OrganizationUser)
        .where(OrganizationUser.company_id == company_id)
        .where(OrganizationUser.open_id == open_id)
        .where(OrganizationUser.status == "active")
    )
    if user is None:
        return {}
    rows = db.execute(
        select(OrganizationMembership, OrganizationDepartment)
        .join(OrganizationDepartment, OrganizationDepartment.id == OrganizationMembership.organization_department_id)
        .where(OrganizationMembership.company_id == company_id)
        .where(OrganizationMembership.organization_user_id == user.id)
        .where(OrganizationMembership.status == "active")
        .where(OrganizationDepartment.status == "active")
    ).all()
    departments = tuple(department for _membership, department in rows)
    primary_department = _primary_subject_department(rows)
    leader_keys = {item for item in (user.source_user_id, user.open_id) if item}
    managed_departments = tuple(
        department
        for department in departments
        if leader_keys and leader_keys & {str(item) for item in (department.leader_source_user_ids or [])}
    )
    return {
        "source": "organization_foundation",
        "actor_user_id": user.source_user_id or user.open_id,
        "actor_open_id": user.open_id,
        "company_id": str(company_id),
        "display_name": user.name,
        "email": user.email or "",
        "job_title": user.job_title or "",
        "department_id": str(primary_department.id) if primary_department is not None else "",
        "department_ids": [str(department.id) for department in departments],
        "department_names": [department.name for department in departments],
        "managed_departments": [
            {"id": str(department.id), "name": department.name, "scope": str(department.unit_type or ORG_TARGET_DEPARTMENT).upper()}
            for department in managed_departments
        ],
        "management_scope": _management_scope_from_departments(managed_departments),
    }


def resolve_department_members(
    db: Session,
    *,
    company_id: UUID,
    query: str,
) -> OrganizationMembersResult | None:
    directory = load_organization_directory(db, company_id=company_id)
    if not directory.departments and not directory.aliases:
        return None
    resolution = resolve_organization_object(query, directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))
    if not resolution.resolved_department_id:
        return OrganizationMembersResult(resolution=resolution, items=())
    try:
        department_id = UUID(resolution.resolved_department_id)
    except ValueError:
        return OrganizationMembersResult(resolution=resolution, items=())
    root_department = db.scalar(
        select(OrganizationDepartment)
        .where(OrganizationDepartment.company_id == company_id)
        .where(OrganizationDepartment.id == department_id)
        .where(OrganizationDepartment.status == "active")
    )
    if root_department is None:
        return OrganizationMembersResult(resolution=resolution, items=())
    subtree_departments = _department_subtree(db, company_id=company_id, root=root_department)
    subtree_ids = tuple(department.id for department in subtree_departments)
    departments_by_id = {department.id: department for department in subtree_departments}
    rows = db.execute(
        select(OrganizationMembership, OrganizationUser)
        .join(OrganizationUser, OrganizationUser.id == OrganizationMembership.organization_user_id)
        .where(OrganizationMembership.company_id == company_id)
        .where(OrganizationMembership.organization_department_id.in_(subtree_ids))
        .where(OrganizationMembership.status == "active")
        .where(OrganizationUser.status == "active")
    ).all()
    items = _dedupe_member_items(
        tuple(
            _member_item(
                user,
                membership=membership,
                resolution=resolution,
                department=departments_by_id.get(membership.organization_department_id),
            )
            for membership, user in rows
        ),
        root_department_name=root_department.name,
    )
    return OrganizationMembersResult(resolution=resolution, items=items)


def resolve_organization_object(
    query: str,
    directory: OrganizationDirectory,
    *,
    target_types: tuple[str, ...] = (ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP, ORG_TARGET_USER),
) -> OrganizationResolution:
    normalized = normalize_organization_name(query)
    if not normalized:
        return OrganizationResolution(query=query, normalized_query="", reason="empty_query", needs_clarification=True)

    alias_candidates = _alias_candidates(normalized, directory, target_types=target_types)
    exact_aliases = tuple(item for item in alias_candidates if item.reason == "alias_exact")
    if len(exact_aliases) == 1 and exact_aliases[0].confidence >= 0.9:
        return _resolved(query, normalized, exact_aliases[0], reason="alias_exact")
    if len(exact_aliases) > 1:
        return OrganizationResolution(
            query=query,
            normalized_query=normalized,
            candidates=exact_aliases,
            reason="alias_ambiguous",
            needs_clarification=True,
        )

    exact = _exact_candidates(normalized, directory, target_types=target_types)
    if len(exact) == 1:
        return _resolved(query, normalized, exact[0], reason="name_exact")
    if len(exact) > 1:
        return OrganizationResolution(
            query=query,
            normalized_query=normalized,
            candidates=exact,
            reason="name_ambiguous",
            needs_clarification=True,
        )

    suffix_candidates = _unit_suffix_candidates(normalized, directory, target_types=target_types)
    if len(suffix_candidates) == 1 and suffix_candidates[0].confidence >= 0.84:
        return _resolved(query, normalized, suffix_candidates[0], reason="unit_suffix_match")
    if len(suffix_candidates) > 1:
        return OrganizationResolution(
            query=query,
            normalized_query=normalized,
            candidates=suffix_candidates,
            reason="unit_suffix_ambiguous",
            needs_clarification=True,
        )

    candidates = (*alias_candidates, *suffix_candidates, *_near_candidates(normalized, directory, target_types=target_types))
    deduped = _dedupe_candidates(candidates)
    return OrganizationResolution(
        query=query,
        normalized_query=normalized,
        candidates=deduped[:5],
        reason="not_resolved" if deduped else "not_found",
        needs_clarification=bool(deduped),
    )


def upsert_organization_snapshot(
    db: Session,
    *,
    company_id: UUID,
    payload: dict[str, Any],
    source_system: str = "feishu",
    sync_type: str = "full",
) -> OrganizationSyncRun:
    now = datetime.now(UTC)
    run = OrganizationSyncRun(
        company_id=company_id,
        source_system=source_system,
        sync_type=sync_type,
        status="running",
        started_at=now,
        cursor={},
        summary={},
    )
    db.add(run)
    db.flush()

    departments = tuple(_department_record(item) for item in payload.get("departments", []) if isinstance(item, dict))
    users = tuple(_user_record(item) for item in payload.get("users", []) if isinstance(item, dict))
    departments_by_source_id: dict[str, OrganizationDepartment] = {}
    users_by_open_id: dict[str, OrganizationUser] = {}

    for item in departments:
        source_department_id = str(item.get("source_department_id") or "").strip()
        if not source_department_id:
            continue
        model = db.scalar(
            select(OrganizationDepartment)
            .where(OrganizationDepartment.company_id == company_id)
            .where(OrganizationDepartment.source_system == source_system)
            .where(OrganizationDepartment.source_department_id == source_department_id)
        )
        if model is None:
            model = OrganizationDepartment(
                company_id=company_id,
                source_system=source_system,
                source_department_id=source_department_id,
                name=str(item.get("name") or source_department_id),
                normalized_name=str(item.get("normalized_name") or normalize_organization_name(item.get("name"))),
            )
            db.add(model)
        _apply_department_record(model, item, now=now)
        departments_by_source_id[source_department_id] = model

    db.flush()

    for item in users:
        open_id = str(item.get("open_id") or "").strip()
        if not open_id:
            continue
        model = db.scalar(
            select(OrganizationUser)
            .where(OrganizationUser.company_id == company_id)
            .where(OrganizationUser.source_system == source_system)
            .where(OrganizationUser.open_id == open_id)
        )
        if model is None:
            model = OrganizationUser(
                company_id=company_id,
                source_system=source_system,
                open_id=open_id,
                name=str(item.get("name") or open_id),
                normalized_name=str(item.get("normalized_name") or normalize_organization_name(item.get("name"))),
            )
            db.add(model)
        _apply_user_record(model, item, now=now)
        users_by_open_id[open_id] = model

    db.flush()

    membership_count = 0
    for user_item in users:
        user = users_by_open_id.get(str(user_item.get("open_id") or "").strip())
        if user is None:
            continue
        for index, source_department_id in enumerate(_string_list(user_item.get("department_ids"))):
            department = departments_by_source_id.get(source_department_id)
            if department is None:
                continue
            membership = db.scalar(
                select(OrganizationMembership)
                .where(OrganizationMembership.company_id == company_id)
                .where(OrganizationMembership.organization_user_id == user.id)
                .where(OrganizationMembership.organization_department_id == department.id)
            )
            if membership is None:
                membership = OrganizationMembership(
                    company_id=company_id,
                    organization_user_id=user.id,
                    organization_department_id=department.id,
                    source_system=source_system,
                    source_payload={},
                )
                db.add(membership)
            membership.is_primary = index == 0
            membership.status = "active"
            membership_count += 1

    run.status = "success"
    run.finished_at = datetime.now(UTC)
    run.department_count = len(departments_by_source_id)
    run.user_count = len(users_by_open_id)
    run.membership_count = membership_count
    run.summary = {
        "department_count": run.department_count,
        "user_count": run.user_count,
        "membership_count": run.membership_count,
    }
    return run


def _resolved(query: str, normalized: str, candidate: OrganizationCandidate, *, reason: str) -> OrganizationResolution:
    return OrganizationResolution(
        query=query,
        normalized_query=normalized,
        resolved_type=candidate.target_type,
        resolved_id=candidate.target_id,
        resolved_name=candidate.name,
        confidence=candidate.confidence,
        candidates=(candidate,),
        reason=reason,
        needs_clarification=False,
    )


def _alias_candidates(
    normalized: str,
    directory: OrganizationDirectory,
    *,
    target_types: tuple[str, ...],
) -> tuple[OrganizationCandidate, ...]:
    candidates = []
    for item in directory.aliases:
        target_type = str(item.get("target_type") or "")
        if target_type not in target_types:
            continue
        alias = str(item.get("normalized_alias") or normalize_organization_name(item.get("alias"))).strip()
        if alias == normalized:
            reason = "alias_exact"
            confidence = float(item.get("confidence") or 0.95)
        elif normalized in alias or alias in normalized:
            reason = "alias_near"
            confidence = min(float(item.get("confidence") or 0.8), 0.82)
        else:
            continue
        candidates.append(
            OrganizationCandidate(
                target_type=target_type,
                target_id=str(item.get("target_id") or ""),
                name=_alias_target_name(item, directory) or str(item.get("name") or item.get("alias") or ""),
                confidence=confidence,
                reason=reason,
                metadata={"source": "alias"},
            )
        )
    return tuple(candidates)


def _exact_candidates(
    normalized: str,
    directory: OrganizationDirectory,
    *,
    target_types: tuple[str, ...],
) -> tuple[OrganizationCandidate, ...]:
    candidates: list[OrganizationCandidate] = []
    if ORG_TARGET_DEPARTMENT in target_types or ORG_TARGET_GROUP in target_types:
        for item in directory.departments:
            item_normalized = str(item.get("normalized_name") or normalize_organization_name(item.get("name")))
            if item_normalized == normalized:
                candidates.append(
                    OrganizationCandidate(
                        target_type=str(item.get("target_type") or ORG_TARGET_DEPARTMENT),
                        target_id=str(item.get("id") or item.get("source_department_id") or ""),
                        name=str(item.get("name") or ""),
                        confidence=0.96,
                        reason="name_exact",
                        metadata={"source": "department"},
                    )
                )
    if ORG_TARGET_USER in target_types:
        for item in directory.users:
            item_normalized = str(item.get("normalized_name") or normalize_organization_name(item.get("name")))
            if item_normalized == normalized:
                candidates.append(
                    OrganizationCandidate(
                        target_type=ORG_TARGET_USER,
                        target_id=str(item.get("id") or item.get("open_id") or ""),
                        name=str(item.get("name") or ""),
                        confidence=0.96,
                        reason="name_exact",
                        metadata={"source": "user"},
                    )
                )
    return tuple(candidates)


def _near_candidates(
    normalized: str,
    directory: OrganizationDirectory,
    *,
    target_types: tuple[str, ...],
) -> tuple[OrganizationCandidate, ...]:
    candidates: list[OrganizationCandidate] = []
    if ORG_TARGET_DEPARTMENT in target_types or ORG_TARGET_GROUP in target_types:
        for item in directory.departments:
            item_normalized = str(item.get("normalized_name") or normalize_organization_name(item.get("name")))
            if not item_normalized or item_normalized == normalized:
                continue
            if normalized in item_normalized or item_normalized in normalized:
                candidates.append(
                    OrganizationCandidate(
                        target_type=str(item.get("target_type") or ORG_TARGET_DEPARTMENT),
                        target_id=str(item.get("id") or item.get("source_department_id") or ""),
                        name=str(item.get("name") or ""),
                        confidence=0.72,
                        reason="name_near",
                        metadata={"source": "department"},
                    )
                )
    if ORG_TARGET_USER in target_types:
        for item in directory.users:
            item_normalized = str(item.get("normalized_name") or normalize_organization_name(item.get("name")))
            if item_normalized and item_normalized != normalized and (normalized in item_normalized or item_normalized in normalized):
                candidates.append(
                    OrganizationCandidate(
                        target_type=ORG_TARGET_USER,
                        target_id=str(item.get("id") or item.get("open_id") or ""),
                        name=str(item.get("name") or ""),
                        confidence=0.72,
                        reason="name_near",
                        metadata={"source": "user"},
                    )
                )
    return tuple(candidates)


def _unit_suffix_candidates(
    normalized: str,
    directory: OrganizationDirectory,
    *,
    target_types: tuple[str, ...],
) -> tuple[OrganizationCandidate, ...]:
    query_stem = _organization_unit_stem(normalized)
    if not query_stem:
        return ()
    candidates: list[OrganizationCandidate] = []
    if ORG_TARGET_DEPARTMENT in target_types or ORG_TARGET_GROUP in target_types:
        for item in directory.departments:
            item_normalized = str(item.get("normalized_name") or normalize_organization_name(item.get("name")))
            item_stem = _organization_unit_stem(item_normalized)
            if item_stem and item_stem == query_stem and item_normalized != normalized:
                candidates.append(
                    OrganizationCandidate(
                        target_type=str(item.get("target_type") or ORG_TARGET_DEPARTMENT),
                        target_id=str(item.get("id") or item.get("source_department_id") or ""),
                        name=str(item.get("name") or ""),
                        confidence=0.84,
                        reason="unit_suffix_match",
                        metadata={"source": "department", "query_stem": query_stem},
                    )
                )
    return _dedupe_candidates(tuple(candidates))


def _organization_unit_stem(normalized: str) -> str:
    for suffix in ("事业部", "部门", "中心", "小组", "团队", "部", "组"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _dedupe_candidates(candidates: tuple[OrganizationCandidate, ...]) -> tuple[OrganizationCandidate, ...]:
    best: dict[tuple[str, str], OrganizationCandidate] = {}
    for item in candidates:
        key = (item.target_type, item.target_id)
        existing = best.get(key)
        if existing is None or item.confidence > existing.confidence:
            best[key] = item
    return tuple(sorted(best.values(), key=lambda item: item.confidence, reverse=True))


def _alias_target_name(item: dict[str, Any], directory: OrganizationDirectory) -> str:
    target_type = str(item.get("target_type") or "")
    target_id = str(item.get("target_id") or "")
    if not target_id:
        return ""
    if target_type in {ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP}:
        for department in directory.departments:
            if str(department.get("id") or department.get("source_department_id") or "") == target_id:
                return str(department.get("name") or "").strip()
    if target_type == ORG_TARGET_USER:
        for user in directory.users:
            if str(user.get("id") or user.get("open_id") or "") == target_id:
                return str(user.get("name") or "").strip()
    return ""


def _department_subtree(
    db: Session,
    *,
    company_id: UUID,
    root: OrganizationDepartment,
) -> tuple[OrganizationDepartment, ...]:
    departments = db.scalars(
        select(OrganizationDepartment)
        .where(OrganizationDepartment.company_id == company_id)
        .where(OrganizationDepartment.status == "active")
    ).all()
    by_parent: dict[str, list[OrganizationDepartment]] = {}
    for department in departments:
        parent_id = str(department.parent_source_department_id or "")
        by_parent.setdefault(parent_id, []).append(department)

    result: list[OrganizationDepartment] = []
    queue = [root]
    seen: set[UUID] = set()
    while queue:
        department = queue.pop(0)
        if department.id in seen:
            continue
        seen.add(department.id)
        result.append(department)
        queue.extend(by_parent.get(str(department.source_department_id or ""), ()))
    return tuple(result)


def _department_record(item: dict[str, Any]) -> dict[str, Any]:
    source_department_id = str(item.get("department_id") or item.get("open_department_id") or item.get("id") or "").strip()
    name = str(item.get("name") or item.get("department_name") or source_department_id).strip()
    parent_id = str(item.get("parent_department_id") or item.get("parent_open_department_id") or "").strip()
    return {
        "id": source_department_id,
        "target_type": _department_target_type(name),
        "source_department_id": source_department_id,
        "open_department_id": str(item.get("open_department_id") or "").strip(),
        "parent_source_department_id": parent_id,
        "name": name,
        "normalized_name": normalize_organization_name(name),
        "unit_type": _department_target_type(name),
        "path_names": _string_list(item.get("path_names") or item.get("department_names")),
        "path_source_department_ids": _string_list(item.get("path_source_department_ids") or item.get("department_ids")),
        "leader_source_user_ids": _string_list(item.get("leader_source_user_ids") or item.get("leader_user_ids")),
        "source_payload": item,
    }


def _user_record(item: dict[str, Any]) -> dict[str, Any]:
    open_id = str(item.get("open_id") or item.get("user_id") or item.get("id") or "").strip()
    name = str(item.get("name") or item.get("display_name") or item.get("en_name") or open_id).strip()
    return {
        "id": open_id,
        "open_id": open_id,
        "source_user_id": str(item.get("user_id") or "").strip(),
        "union_id": str(item.get("union_id") or "").strip(),
        "name": name,
        "normalized_name": normalize_organization_name(name),
        "email": str(item.get("email") or "").strip(),
        "mobile": str(item.get("mobile") or item.get("phone") or "").strip(),
        "job_title": str(item.get("title") or item.get("job_title") or "").strip(),
        "department_ids": _string_list(item.get("department_ids")),
        "department_names": _string_list(item.get("department_names")),
        "status": _user_status(item.get("status")),
        "source_payload": item,
    }


def _department_model_record(item: OrganizationDepartment) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "target_type": item.unit_type or ORG_TARGET_DEPARTMENT,
        "source_department_id": item.source_department_id,
        "open_department_id": item.open_department_id,
        "name": item.name,
        "normalized_name": item.normalized_name,
        "path_names": item.path_names or [],
    }


def _user_model_record(item: OrganizationUser) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "open_id": item.open_id,
        "source_user_id": item.source_user_id,
        "name": item.name,
        "normalized_name": item.normalized_name,
        "email": item.email,
        "mobile": item.mobile,
        "job_title": item.job_title,
    }


def _alias_model_record(item: OrganizationAlias) -> dict[str, Any]:
    return {
        "alias": item.alias,
        "normalized_alias": item.normalized_alias,
        "target_type": item.target_type,
        "target_id": str(item.target_id),
        "confidence": item.confidence,
        "name": item.alias,
    }


def _member_item(
    user: OrganizationUser,
    *,
    membership: OrganizationMembership,
    resolution: OrganizationResolution,
    department: OrganizationDepartment | None = None,
) -> dict[str, Any]:
    department_name = department.name if department is not None else resolution.resolved_name
    department_id = str(department.id) if department is not None else resolution.resolved_department_id
    return {
        "name": user.name,
        "open_id": user.open_id,
        "user_id": user.source_user_id or "",
        "email": user.email or "",
        "mobile": user.mobile or "",
        "title": user.job_title or "",
        "department": department_name,
        "department_names": [department_name] if department_name else [],
        "department_ids": [department_id] if department_id else [],
        "is_primary_department": membership.is_primary,
        "source_system": user.source_system,
        "resource_plane": "foundation",
        "resource_type": "organization_user",
    }


def _dedupe_member_items(
    items: tuple[dict[str, Any], ...],
    *,
    root_department_name: str,
) -> tuple[dict[str, Any], ...]:
    best: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("open_id") or item.get("user_id") or item.get("name") or "").strip()
        if not key:
            continue
        existing = best.get(key)
        if existing is None:
            best[key] = item
            continue
        if str(item.get("department") or "") == root_department_name:
            best[key] = item
    return tuple(best.values())


def _primary_subject_department(rows: list[tuple[OrganizationMembership, OrganizationDepartment]]) -> OrganizationDepartment | None:
    if not rows:
        return None
    for membership, department in rows:
        if membership.is_primary:
            return department
    return rows[0][1]


def _management_scope_from_departments(departments: tuple[OrganizationDepartment, ...]) -> list[dict[str, str]]:
    return [
        {
            "scope": str(department.unit_type or ORG_TARGET_DEPARTMENT).upper(),
            "department_id": str(department.id),
            "department_name": department.name,
        }
        for department in departments
    ]


def _apply_department_record(model: OrganizationDepartment, item: dict[str, Any], *, now: datetime) -> None:
    model.open_department_id = str(item.get("open_department_id") or "") or None
    model.parent_source_department_id = str(item.get("parent_source_department_id") or "") or None
    model.name = str(item.get("name") or model.name)
    model.normalized_name = str(item.get("normalized_name") or normalize_organization_name(model.name))
    model.unit_type = str(item.get("unit_type") or ORG_TARGET_DEPARTMENT)
    model.status = str(item.get("status") or "active")
    model.path_names = _string_list(item.get("path_names"))
    model.path_source_department_ids = _string_list(item.get("path_source_department_ids"))
    model.leader_source_user_ids = _string_list(item.get("leader_source_user_ids"))
    model.source_payload = dict(item.get("source_payload") or {})
    model.last_synced_at = now


def _apply_user_record(model: OrganizationUser, item: dict[str, Any], *, now: datetime) -> None:
    model.source_user_id = str(item.get("source_user_id") or "") or None
    model.union_id = str(item.get("union_id") or "") or None
    model.name = str(item.get("name") or model.name)
    model.normalized_name = str(item.get("normalized_name") or normalize_organization_name(model.name))
    model.email = str(item.get("email") or "") or None
    model.mobile = str(item.get("mobile") or "") or None
    model.job_title = str(item.get("job_title") or "") or None
    model.status = str(item.get("status") or "active")
    model.source_payload = dict(item.get("source_payload") or {})
    model.metadata_json = {"department_ids": _string_list(item.get("department_ids")), "department_names": _string_list(item.get("department_names"))}
    model.last_synced_at = now


def _department_target_type(name: str) -> str:
    normalized = normalize_organization_name(name)
    if normalized.endswith("组"):
        return ORG_TARGET_GROUP
    return ORG_TARGET_DEPARTMENT


def _user_status(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("is_exited") or value.get("is_resigned"):
            return "exited"
        if value.get("is_frozen"):
            return "frozen"
        if value.get("is_unjoin"):
            return "unjoined"
        if value.get("is_activated") is True:
            return "active"
        if value.get("is_activated") is False:
            return "inactive"
        return "unknown"
    text = str(value or "").strip().lower()
    if not text:
        return "active"
    if text in {"active", "enabled", "normal"}:
        return "active"
    if text in {"exited", "resigned", "deleted"}:
        return "exited"
    if text in {"frozen", "disabled", "inactive", "unjoined", "unknown"}:
        return text
    return text[:40]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]
