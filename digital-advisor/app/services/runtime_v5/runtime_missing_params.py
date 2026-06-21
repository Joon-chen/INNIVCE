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
    prefixes=("原因是", "原因：", "原因:", "理由是", "理由：", "理由:", "备注：", "备注:"),
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
    return contract.resolver.resolve(str(text or ""))


def message_with_text_param(message: str, value: str, *, filled_param: str) -> str:
    base = message.strip() or "拒绝这个审批"
    if filled_param != "comment" or "原因" in base or "理由" in base:
        return base
    return f"{base}，原因：{value}"
