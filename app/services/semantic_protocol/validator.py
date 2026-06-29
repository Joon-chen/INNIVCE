from __future__ import annotations

from dataclasses import fields

from app.services.semantic_protocol.enums import Operation, OutputMode, SpeechAct
from app.services.semantic_protocol.frame import SemanticFrame
from app.services.semantic_protocol.schema import SemanticFrameSchema


class SemanticValidationError(ValueError):
    pass


def validate_semantic_frame(frame: SemanticFrame, *, schema: SemanticFrameSchema | None = None) -> SemanticFrame:
    schema = schema or SemanticFrameSchema()
    payload = frame.payload()
    missing = [name for name in schema.required_fields if name not in payload]
    if missing:
        raise SemanticValidationError(f"SemanticFrame missing required fields: {', '.join(missing)}")

    field_names = {item.name for item in fields(SemanticFrame)}
    forbidden = [name for name in schema.forbidden_fields if name in payload or name in field_names]
    if forbidden:
        raise SemanticValidationError(f"SemanticFrame contains forbidden fields: {', '.join(forbidden)}")

    _validate_enum("speech_act", frame.speech_act, {item.value for item in SpeechAct})
    _validate_enum("operation", frame.operation, {item.value for item in Operation})
    _validate_enum("requested_output", frame.requested_output, {item.value for item in OutputMode})
    if not isinstance(frame.target, dict):
        raise SemanticValidationError("SemanticFrame.target must be a dict")
    if not isinstance(frame.parameters, dict):
        raise SemanticValidationError("SemanticFrame.parameters must be a dict")
    if not 0.0 <= float(frame.confidence) <= 1.0:
        raise SemanticValidationError("SemanticFrame.confidence must be between 0 and 1")
    if not isinstance(frame.ambiguities, tuple):
        raise SemanticValidationError("SemanticFrame.ambiguities must be a tuple")
    return frame


def _validate_enum(name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        raise SemanticValidationError(f"SemanticFrame.{name} is invalid: {value}")
