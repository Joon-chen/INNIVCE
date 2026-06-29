from __future__ import annotations

import re


PEOPLE_FIELD_ALIASES: dict[str, str] = {
    "phone": "mobile",
    "telephone": "mobile",
    "mobile": "mobile",
    "手机号": "mobile",
    "电话": "mobile",
    "号码": "mobile",
    "手机": "mobile",
    "email": "email",
    "mail": "email",
    "邮箱": "email",
    "position": "title",
    "job_title": "title",
    "title": "title",
    "role": "title",
    "岗位": "title",
    "职位": "title",
    "职务": "title",
    "leader": "leader",
    "manager": "leader",
    "supervisor": "leader",
    "直属上级": "leader",
    "上级": "leader",
    "领导": "leader",
    "负责人": "leader",
    "gender": "gender",
    "sex": "gender",
    "性别": "gender",
}


PEOPLE_FIELD_LABELS: dict[str, str] = {
    "mobile": "手机号",
    "email": "邮箱",
    "title": "岗位",
    "leader": "直属上级",
    "gender": "性别",
    "profile": "信息",
}


def canonical_people_field(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return PEOPLE_FIELD_ALIASES.get(normalized, normalized)


def people_field_label(field: str) -> str:
    return PEOPLE_FIELD_LABELS.get(canonical_people_field(field), "信息")


def people_fields_from_text(text: str) -> tuple[str, ...]:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    fields: list[str] = []
    for canonical, aliases in (
        ("title", ("岗位", "职位", "职务")),
        ("leader", ("直属上级", "上级", "领导", "负责人")),
        ("mobile", ("电话", "号码", "手机号", "手机")),
        ("email", ("邮箱",)),
        ("gender", ("男还是女", "女还是男", "男性还是女性", "是男是女", "性别")),
    ):
        if any(alias in compact for alias in aliases):
            fields.append(canonical)
    return tuple(dict.fromkeys(fields))
