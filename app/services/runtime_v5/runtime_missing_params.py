from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


MissingParamType = Literal["TEXT", "USER"]


@dataclass(frozen=True)
class MissingParamResolver:
    param_type: MissingParamType
    prefixes: tuple[str, ...] = ()

    def resolve(self, text: str) -> str:
        cleaned = text.strip()
        for prefix in self.prefixes:
            if cleaned.startswith(prefix):
                return cleaned.removeprefix(prefix).strip()
        return cleaned


@dataclass(frozen=True)
class MissingParamContract:
    name: str
    param_type: MissingParamType
    target_key: str
    filled_param: str
    resolver: MissingParamResolver


TEXT: MissingParamType = "TEXT"
USER: MissingParamType = "USER"
_TEXT_RESOLVER = MissingParamResolver(
    param_type=TEXT,
    prefixes=("内容是", "内容：", "内容:", "消息是", "消息：", "消息:", "说：", "说:", "原因是", "原因：", "原因:", "理由是", "理由：", "理由:", "备注：", "备注:"),
)
_TARGET_RESOLVER = MissingParamResolver(
    param_type=TEXT,
    prefixes=("发给", "发送给", "给", "发到", "发送到", "@"),
)
_USER_RESOLVER = MissingParamResolver(
    param_type=USER,
    prefixes=("转交给", "转交", "加签给", "加签", "给", "@"),
)
_MISSING_PARAM_REGISTRY: dict[str, MissingParamContract] = {
    "comment": MissingParamContract(
        name="comment",
        param_type=TEXT,
        target_key="comment",
        filled_param="comment",
        resolver=_TEXT_RESOLVER,
    ),
    "target_user": MissingParamContract(
        name="target_user",
        param_type=USER,
        target_key="target_user",
        filled_param="target_user",
        resolver=_USER_RESOLVER,
    ),
    "text": MissingParamContract(
        name="text",
        param_type=TEXT,
        target_key="text",
        filled_param="text",
        resolver=_TEXT_RESOLVER,
    ),
    "target_type": MissingParamContract(
        name="target_type",
        param_type=TEXT,
        target_key="target",
        filled_param="target",
        resolver=_TARGET_RESOLVER,
    ),
    "delivery_mode": MissingParamContract(
        name="delivery_mode",
        param_type=TEXT,
        target_key="delivery_mode",
        filled_param="delivery_mode",
        resolver=_TEXT_RESOLVER,
    ),
}


def action_input_missing_params(action_input) -> list[str]:
    missing = action_input.metadata.get("missing_params") if isinstance(action_input.metadata, dict) else []
    if not isinstance(missing, list):
        return []
    return [str(item).strip() for item in missing if str(item).strip()]


def pending_action_with_user_input(pending_action: dict[str, Any], user_input: str) -> dict[str, Any]:
    text = str(user_input or "").strip()
    action_input = pending_action.get("runtime_action_input") if isinstance(pending_action.get("runtime_action_input"), dict) else {}
    target = action_input.get("target") if isinstance(action_input.get("target"), dict) else {}
    entities = pending_action.get("entities") if isinstance(pending_action.get("entities"), dict) else {}
    contract = missing_param_contract(pending_action)
    value = resolve_missing_param_value(contract, text) if contract else text
    target_key = contract.target_key if contract else "comment"
    filled_param = contract.filled_param if contract else target_key
    message = message_with_text_param(str(pending_action.get("message") or ""), value, filled_param=filled_param)
    updated_target = {**target, target_key: value}
    updated_action_input = {
        **action_input,
        "target": updated_target,
        "message": message,
        "metadata": {
            **(action_input.get("metadata") if isinstance(action_input.get("metadata"), dict) else {}),
            "missing_params": [],
            "filled_params": {filled_param: value},
        },
    }
    return {
        **pending_action,
        "message": message,
        "entities": {**entities, target_key: value},
        "missing_params": [],
        "input_contract": {
            "status": "waiting_confirmation",
            "missing_params": [],
            "filled_params": [filled_param],
        },
        "runtime_action_input": updated_action_input,
    }


def waiting_input_still_missing(pending_action: dict[str, Any], user_input: str) -> bool:
    contract = missing_param_contract(pending_action)
    if contract is None:
        return False
    return not resolve_missing_param_value(contract, user_input)


def missing_param_contract(pending_action: dict[str, Any]) -> MissingParamContract | None:
    missing_params = [str(item) for item in pending_action.get("missing_params", []) if str(item)]
    for param in missing_params:
        contract = _MISSING_PARAM_REGISTRY.get(param)
        if contract is not None:
            return contract
    return None


def resolve_missing_param_value(contract: MissingParamContract, text: str) -> str:
    value = contract.resolver.resolve(str(text or ""))
    if contract.name == "delivery_mode":
        return _normalize_delivery_mode(value)
    return value


def message_with_text_param(message: str, value: str, *, filled_param: str) -> str:
    base = message.strip() or ("拒绝这个审批" if filled_param == "comment" else "")
    if filled_param == "text":
        return _message_with_message_text(base, value)
    if filled_param == "target":
        return _message_with_target(base, value)
    if filled_param == "delivery_mode":
        return _message_with_delivery_mode(base, value)
    if filled_param != "comment" or "原因" in base or "理由" in base:
        return base
    return f"{base}，原因：{value}"


def _message_with_message_text(message: str, value: str) -> str:
    base = message.strip()
    if not base:
        return value
    if any(marker in base for marker in ("说：", "说:", "内容是", "消息是")):
        return base
    return f"{base}说：{value}"


def _message_with_target(message: str, value: str) -> str:
    base = message.strip()
    target = value.strip()
    if target in {"这里", "当前会话", "这个群"}:
        return f"发到{target}{base}"
    if not base:
        return f"发给{target}"
    if base.startswith(("发消息", "发送消息", "发信息", "发送信息")):
        return f"给{target}{base}"
    return f"发给{target} {base}"


def _message_with_delivery_mode(message: str, value: str) -> str:
    base = message.strip()
    mode = _delivery_mode_phrase(value)
    if not base:
        return mode
    if mode in base:
        return base
    return f"{mode}，{base}"


def _normalize_delivery_mode(value: str) -> str:
    compact = "".join(str(value or "").split()).lower()
    if compact in {"bot_multi_notify", "user_multi_private", "create_group_then_send"}:
        return compact
    if any(token in compact for token in ("机器人通知", "用机器人", "机器人发", "系统通知", "自动通知", "大飞哥通知")):
        return "bot_multi_notify"
    if any(token in compact for token in ("替我发", "用我", "以我的名义", "我发给", "分别发", "单独发", "单独发送", "私聊发")):
        return "user_multi_private"
    if any(token in compact for token in ("拉群", "建群", "建个群", "创建群", "群里发", "发到群")):
        return "create_group_then_send"
    return str(value or "").strip()


def _delivery_mode_phrase(value: str) -> str:
    mode = _normalize_delivery_mode(value)
    return {
        "bot_multi_notify": "用机器人通知这些人",
        "user_multi_private": "替我分别发给这些人",
        "create_group_then_send": "拉群后发到群里",
    }.get(mode, str(value or "").strip())
