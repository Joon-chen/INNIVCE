from __future__ import annotations

from typing import Any


def prompt_audit_payload(*, prompt: str, task_type: str, lane: str = "") -> dict[str, Any]:
    text = str(prompt or "")
    has_intent_profile = "Intent Profile：" in text or "Intent Profile:" in text
    has_presentation_profile = "用户画像：" in text
    has_profile = has_intent_profile or has_presentation_profile
    has_session_context = "最近对话上下文：" in text or "会话上下文" in text or "Conversation Context:" in text
    has_original_answer = "原答案：" in text or "系统原始回复：" in text
    profile_plane = "none"
    if has_intent_profile and has_presentation_profile:
        profile_plane = "mixed"
    elif has_intent_profile:
        profile_plane = "intent"
    elif has_presentation_profile:
        profile_plane = "presentation"
    line_count = len(text.splitlines())
    prompt_chars = len(text)
    risks: list[str] = []
    if prompt_chars > 3000:
        risks.append("prompt_long")
    if line_count > 80:
        risks.append("prompt_many_lines")
    if has_original_answer and task_type == "command_intent":
        risks.append("command_with_answer_context")
    if has_presentation_profile and task_type == "command_intent":
        risks.append("command_with_presentation_profile")
    if has_intent_profile and task_type in {"presentation", "conversation"}:
        risks.append("output_with_intent_profile")
    if profile_plane == "mixed":
        risks.append("mixed_profile_planes")
    return {
        "task_type": task_type,
        "lane": lane,
        "prompt_chars": prompt_chars,
        "line_count": line_count,
        "profile_plane": profile_plane,
        "has_profile_context": has_profile,
        "has_intent_profile_context": has_intent_profile,
        "has_presentation_profile_context": has_presentation_profile,
        "has_session_context": has_session_context,
        "has_original_answer": has_original_answer,
        "risk_count": len(risks),
        "risks": risks,
    }
