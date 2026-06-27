from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Mapping

import redis
from sqlalchemy import text as sql

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.identity_fact import identity_fact_from_context


PROFILE_DEFAULTS: dict[str, Any] = {
    "style": "professional",
    "verbosity": "balanced",
    "use_emoji": False,
    "use_formatting": True,
    "tone_tips": "",
    "preferred_address": "",
    "avoid_direct_name": False,
    "interactions": 0,
    "last_updated": 0.0,
}
PROFILE_TTL_SECONDS = 86400


@dataclass(frozen=True)
class IntentProfile:
    open_id: str = ""
    display_name: str = ""
    role: str = ""
    departments: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    common_scope: str = ""
    business_aliases: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PresentationProfile:
    style: str = "professional"
    verbosity: str = "balanced"
    use_emoji: bool = False
    use_formatting: bool = True
    tone_tips: str = ""
    preferred_address: str = ""
    avoid_direct_name: bool = False
    actor_style: str = ""


@dataclass(frozen=True)
class ProfileContext:
    open_id: str = ""
    intent: IntentProfile = field(default_factory=IntentProfile)
    presentation: PresentationProfile = field(default_factory=PresentationProfile)
    source: str = "default"


def load_profile_context(
    *,
    open_id: str = "",
    runtime_context: Any | None = None,
    actor_style: str = "",
    profile_payload: Mapping[str, Any] | None = None,
) -> ProfileContext:
    identity_fact = identity_fact_from_context(runtime_context)
    resolved_open_id = open_id or identity_fact.open_id
    payload = dict(profile_payload or load_profile_payload(resolved_open_id))
    source = "user_profile" if payload else "default"
    role = identity_fact.role or str(payload.get("role") or "")
    display_name = identity_fact.display_name or str(payload.get("display_name") or "")
    departments = identity_fact.department_names or tuple(
        item
        for item in (
            str(payload.get("department_name") or ""),
            str(payload.get("department_id") or ""),
        )
        if item
    )
    domains = identity_fact.domains or tuple(str(item) for item in (payload.get("domains") or ()) if str(item))
    aliases = payload.get("business_aliases") if isinstance(payload.get("business_aliases"), dict) else {}
    common_scope = str(payload.get("common_scope") or payload.get("preferred_scope") or "")
    return ProfileContext(
        open_id=resolved_open_id,
        intent=IntentProfile(
            open_id=resolved_open_id,
            display_name=display_name,
            role=role,
            departments=departments,
            domains=domains,
            common_scope=common_scope,
            business_aliases={str(k): str(v) for k, v in aliases.items()},
        ),
        presentation=PresentationProfile(
            style=str(payload.get("style") or PROFILE_DEFAULTS["style"]),
            verbosity=str(payload.get("verbosity") or PROFILE_DEFAULTS["verbosity"]),
            use_emoji=bool(payload.get("use_emoji", PROFILE_DEFAULTS["use_emoji"])),
            use_formatting=bool(payload.get("use_formatting", PROFILE_DEFAULTS["use_formatting"])),
            tone_tips=str(payload.get("tone_tips") or ""),
            preferred_address=str(payload.get("preferred_address") or ""),
            avoid_direct_name=bool(payload.get("avoid_direct_name", PROFILE_DEFAULTS["avoid_direct_name"])),
            actor_style=actor_style,
        ),
        source=source,
    )


def intent_profile_text(profile_context: ProfileContext) -> str:
    profile = profile_context.intent
    parts: list[str] = []
    if profile.display_name:
        parts.append(f"用户称呼：{profile.display_name}")
    if profile.role:
        parts.append(f"用户角色：{profile.role}")
    if profile.domains:
        parts.append(f"常见业务域：{', '.join(profile.domains[:8])}")
    if profile.common_scope:
        parts.append(f"常用查询范围：{profile.common_scope}")
    if profile.business_aliases:
        aliases = ", ".join(f"{key}={value}" for key, value in list(profile.business_aliases.items())[:8])
        parts.append(f"业务别名：{aliases}")
    parts.append("边界：画像只辅助理解意图和称呼，不授予权限，不改变执行身份")
    return "；".join(parts)


def presentation_profile_text(profile_context: ProfileContext) -> str:
    profile = profile_context.presentation
    parts = [f"说话风格：{profile.style}", f"详细程度：{profile.verbosity}"]
    if profile.use_emoji:
        parts.append("可以适当使用emoji")
    if profile.tone_tips:
        parts.append(f"额外提示：{profile.tone_tips}")
    if profile.preferred_address:
        parts.append(f"称呼偏好：称呼用户为「{profile.preferred_address}」")
    if profile.avoid_direct_name:
        parts.append("称呼边界：不要直呼用户真实姓名")
    if profile.actor_style:
        parts.append(f"角色提示：{profile.actor_style}")
    parts.append("边界：只调整表达方式，不改变事实、权限、数量或动作")
    return "；".join(parts)


def load_profile_payload(open_id: str) -> dict[str, Any]:
    if not open_id:
        return dict(PROFILE_DEFAULTS)
    _ensure_profile_table()
    try:
        raw = _redis_client().get(_profile_key(open_id))
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return {**PROFILE_DEFAULTS, **payload}
    except Exception:
        pass
    try:
        db = SessionLocal()
        try:
            row = db.execute(sql("SELECT profile_json FROM user_profiles WHERE open_id = :open_id"), {"open_id": open_id}).fetchone()
            if row and row[0]:
                payload = json.loads(row[0])
                if isinstance(payload, dict):
                    merged = {**PROFILE_DEFAULTS, **payload}
                    _cache_profile(open_id, merged)
                    return merged
        finally:
            db.close()
    except Exception:
        pass
    return dict(PROFILE_DEFAULTS)


def save_profile_payload(open_id: str, payload: Mapping[str, Any]) -> None:
    if not open_id:
        return
    _ensure_profile_table()
    profile = {**PROFILE_DEFAULTS, **dict(payload)}
    profile["interactions"] = int(profile.get("interactions") or 0)
    profile["last_updated"] = float(profile.get("last_updated") or time.time())
    _cache_profile(open_id, profile)
    try:
        db = SessionLocal()
        try:
            profile_json = json.dumps(profile, ensure_ascii=False)
            existing = db.execute(sql("SELECT 1 FROM user_profiles WHERE open_id = :open_id"), {"open_id": open_id}).fetchone()
            if existing:
                db.execute(
                    sql("UPDATE user_profiles SET profile_json = :profile_json, updated_at = NOW() WHERE open_id = :open_id"),
                    {"profile_json": profile_json, "open_id": open_id},
                )
            else:
                db.execute(
                    sql("INSERT INTO user_profiles (open_id, profile_json, created_at, updated_at) VALUES (:open_id, :profile_json, NOW(), NOW())"),
                    {"profile_json": profile_json, "open_id": open_id},
                )
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def apply_profile_update(open_id: str, update: Mapping[str, Any]) -> dict[str, Any]:
    """Persist safe expression preferences extracted by Command LLM.

    This stores user communication preferences only. It does not change identity
    facts, roles, permissions, scope, or execution identity.
    """
    if not open_id or not isinstance(update, Mapping):
        return {}
    current = load_profile_payload(open_id)
    changed: dict[str, Any] = {}
    preferred_address = str(update.get("preferred_address") or "").strip()
    if preferred_address and len(preferred_address) <= 30:
        changed["preferred_address"] = preferred_address
    if "avoid_direct_name" in update:
        changed["avoid_direct_name"] = bool(update.get("avoid_direct_name"))
    tone_tips = str(update.get("tone_tips") or "").strip()
    if tone_tips and len(tone_tips) <= 200:
        changed["tone_tips"] = tone_tips
    style = str(update.get("style") or "").strip()
    if style in {"professional", "casual", "formal", "direct", "warm"}:
        changed["style"] = style
    verbosity = str(update.get("verbosity") or "").strip()
    if verbosity in {"concise", "balanced", "detailed"}:
        changed["verbosity"] = verbosity
    if not changed:
        return {}
    current.update(changed)
    current["interactions"] = int(current.get("interactions") or 0) + 1
    current["last_updated"] = time.time()
    save_profile_payload(open_id, current)
    return changed


def _profile_key(open_id: str) -> str:
    return f"feishu:profile:{open_id}"


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _cache_profile(open_id: str, payload: Mapping[str, Any]) -> None:
    try:
        _redis_client().setex(_profile_key(open_id), PROFILE_TTL_SECONDS, json.dumps(dict(payload), ensure_ascii=False))
    except Exception:
        pass


def _ensure_profile_table() -> None:
    try:
        db = SessionLocal()
        try:
            db.execute(sql("CREATE TABLE IF NOT EXISTS user_profiles (open_id VARCHAR PRIMARY KEY, profile_json TEXT DEFAULT '{}', created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())"))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
