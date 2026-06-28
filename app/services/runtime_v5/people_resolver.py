from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


PEOPLE_RESULT_TYPES = {"people_search", "department_members", "organization_snapshot"}


@dataclass(frozen=True)
class PeopleResolveResult:
    query: str
    items: tuple[dict[str, Any], ...]
    match_type: str = ""

    @property
    def count(self) -> int:
        return len(self.items)


def normalize_people_item(user: dict[str, Any]) -> dict[str, Any]:
    department_names = _string_list(user.get("department_names"))
    department_ids = _string_list(user.get("department_ids"))
    raw_gender = user.get("gender") or user.get("gender_name") or user.get("sex") or ""
    name = user.get("name") or user.get("display_name") or user.get("english_name") or user.get("open_id") or ""
    title = user.get("title") or user.get("job_title") or ""
    gender = normalize_gender(raw_gender)
    return {
        "name": name,
        "department": ", ".join(department_names),
        "department_names": department_names,
        "department_ids": ", ".join(department_ids),
        "department_id_list": department_ids,
        "leader": user.get("leader") or user.get("manager") or user.get("leader_name") or user.get("manager_name") or "",
        "leader_name": user.get("leader_name") or user.get("manager_name") or user.get("leader") or user.get("manager") or "",
        "leader_user_id": user.get("leader_user_id") or user.get("manager_user_id") or "",
        "employee_no": user.get("employee_no") or user.get("employee_id") or "",
        "title": title,
        "job_title": title,
        "title_source": "source" if title else "",
        "gender": str(raw_gender or "").strip(),
        "gender_normalized": gender,
        "gender_source": "source" if gender else "",
        "email": user.get("email") or "",
        "mobile": user.get("mobile") or "",
        "open_id": user.get("open_id") or "",
        "user_id": user.get("user_id") or "",
    }


def normalize_people_items(users: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(users, list):
        return ()
    return tuple(normalize_people_item(user) for user in users if isinstance(user, dict))


def resolve_people_from_items(query: str, items: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> PeopleResolveResult:
    normalized_query = _normalize_query(query)
    people = tuple(item for item in items if isinstance(item, dict))
    if not normalized_query:
        return PeopleResolveResult(query=query, items=(), match_type="")
    exact = tuple(item for item in people if _field_equal(item, normalized_query, ("open_id", "user_id", "email", "mobile", "name")))
    if exact:
        return PeopleResolveResult(query=query, items=exact, match_type="exact_identity")
    fuzzy = tuple(item for item in people if _person_matches(item, normalized_query))
    if fuzzy:
        return PeopleResolveResult(query=query, items=fuzzy, match_type="fuzzy_identity")
    candidates = _near_name_candidates(normalized_query, people)
    return PeopleResolveResult(query=query, items=candidates, match_type="near_identity_candidate" if candidates else "")


def filter_people_by_department(items: tuple[dict[str, Any], ...] | list[dict[str, Any]], keyword: str) -> tuple[dict[str, Any], ...]:
    normalized = _department_keyword(keyword)
    people = tuple(item for item in items if isinstance(item, dict))
    if not normalized:
        return people
    return tuple(item for item in people if _department_matches(item, normalized))


def filter_people_by_gender(items: tuple[dict[str, Any], ...] | list[dict[str, Any]], gender: str) -> tuple[dict[str, Any], ...]:
    normalized = normalize_gender(gender)
    if not normalized:
        return ()
    return tuple(item for item in items if isinstance(item, dict) and normalize_gender(item.get("gender_normalized") or item.get("gender")) == normalized)


def filter_people_by_title(items: tuple[dict[str, Any], ...] | list[dict[str, Any]], keyword: str) -> tuple[dict[str, Any], ...]:
    normalized = _title_keyword(keyword)
    if not normalized:
        return ()
    people = tuple(item for item in items if isinstance(item, dict))
    return tuple(item for item in people if normalized in _title_keyword(str(item.get("title") or "")))


def gender_filter_from_text(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if any(token in compact for token in ("男生", "男性", "男的", "男人", "男员工")):
        return "male"
    if any(token in compact for token in ("女生", "女性", "女的", "女人", "女员工")):
        return "female"
    return ""


def asks_people_list(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    return any(token in compact for token in ("分别是谁", "都有谁", "是谁", "名单", "列出", "全部", "显示"))


def normalize_gender(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "0", "unknown", "未知", "未设置", "保密"}:
        return ""
    if text in {"1", "male", "m", "男", "男性", "男生"}:
        return "male"
    if text in {"2", "female", "f", "女", "女性", "女生"}:
        return "female"
    return text


def format_people_brief(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "未知人员").strip()
    details = []
    title = str(item.get("title") or "").strip()
    department = str(item.get("department") or "").strip()
    if title:
        details.append(title)
    if department:
        details.append(department)
    return name + (f"（{'，'.join(details)}）" if details else "")


def format_people_detail(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "未知人员").strip()
    parts = []
    title = str(item.get("title") or "").strip()
    department = str(item.get("department") or "").strip()
    email = str(item.get("email") or "").strip()
    mobile = str(item.get("mobile") or "").strip()
    if title:
        parts.append(f"职位：{title}")
    if department:
        parts.append(f"部门：{department}")
    if email:
        parts.append(f"邮箱：{email}")
    if mobile:
        parts.append(f"手机：{mobile}")
    return name + (f"｜{'｜'.join(parts)}" if parts else "")


def people_context_metadata(*, capability: str = "people.resolve_identity", **extra: Any) -> dict[str, Any]:
    return {
        "entity_domain": "people",
        "resolver": "people_resolver",
        "resolver_capability": capability,
        "context_schema": "people_result_context_v1",
        "sidepanel_view": "people_detail",
        **extra,
    }


def people_targets_from_result_context(result_context: Any, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
    if result_context is None or getattr(result_context, "result_type", "") not in PEOPLE_RESULT_TYPES:
        return ()
    targets: list[dict[str, Any]] = []
    for item in getattr(result_context, "items", ()) or ():
        if not isinstance(item, dict):
            continue
        open_id = str(item.get("open_id") or item.get("user_id") or "").strip()
        email = str(item.get("email") or "").strip()
        if not open_id and not email:
            continue
        targets.append(
            {
                "name": str(item.get("name") or "").strip(),
                "open_id": open_id,
                "user_id": str(item.get("user_id") or "").strip(),
                "email": email,
                "department": str(item.get("department") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "source": "result_context",
                "match_type": "context_identity",
            }
        )
        if len(targets) >= limit:
            break
    return tuple(targets)


def references_people_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    return any(token in compact for token in ("他们", "她们", "这些人", "这批人", "刚才那些人", "上面这些人", "全部人", "所有人"))


def _person_matches(item: dict[str, Any], normalized_query: str) -> bool:
    haystack = " ".join(
        str(item.get(key) or "").lower()
        for key in ("name", "email", "mobile", "title", "department", "open_id", "user_id")
    )
    return normalized_query in haystack


def _near_name_candidates(normalized_query: str, people: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    if len(normalized_query) < 2 or len(normalized_query) > 12:
        return ()
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in people:
        name = str(item.get("name") or "").strip().lower()
        if not name or abs(len(name) - len(normalized_query)) > 1:
            continue
        ratio = SequenceMatcher(None, normalized_query, name).ratio()
        if ratio >= 0.66:
            scored.append((ratio, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(item for _, item in scored[:3])


def _department_matches(item: dict[str, Any], normalized_keyword: str) -> bool:
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("department", "department_ids", "name", "title")
    )
    return normalized_keyword in _department_keyword(haystack)


def _field_equal(item: dict[str, Any], normalized_query: str, keys: tuple[str, ...]) -> bool:
    return any(str(item.get(key) or "").strip().lower() == normalized_query for key in keys)


def _normalize_query(value: str) -> str:
    return str(value or "").strip().lower()


def _department_keyword(value: str) -> str:
    return (
        str(value or "")
        .replace("部门", "")
        .replace("团队", "")
        .replace("中心", "")
        .replace("小组", "")
        .replace(" ", "")
        .strip()
    )


def _title_keyword(value: str) -> str:
    return (
        str(value or "")
        .replace("岗位", "")
        .replace("职位", "")
        .replace("职务", "")
        .replace("人员", "")
        .replace("员工", "")
        .replace("有哪些", "")
        .replace("都有谁", "")
        .replace("是谁", "")
        .replace("哪些是", "")
        .replace("谁是", "")
        .replace("公司", "")
        .replace("部门", "")
        .replace(" ", "")
        .strip()
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []
