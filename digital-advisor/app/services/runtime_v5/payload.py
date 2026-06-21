from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any
from uuid import UUID


def runtime_v5_payload(value: Any) -> Any:
    if is_dataclass(value):
        return runtime_v5_payload(asdict(value))
    if isinstance(value, dict):
        return {str(key): runtime_v5_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [runtime_v5_payload(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    return value
