from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.services.semantic_protocol import (
    Operation,
    OutputMode,
    SemanticFrame,
    SemanticValidationError,
    SpeechAct,
    validate_semantic_frame,
)


def test_semantic_protocol_frame_is_business_neutral_contract() -> None:
    frame = validate_semantic_frame(
        SemanticFrame(
            speech_act=SpeechAct.ASK.value,
            topic="any_domain_topic",
            target={"kind": "collection"},
            operation=Operation.COUNT.value,
            requested_output=OutputMode.NUMERIC_ONLY.value,
            parameters={"raw_message": "anything"},
            confidence=0.9,
        )
    )

    payload = frame.payload()

    assert payload["topic"] == "any_domain_topic"
    assert "capability" not in payload
    assert "provider" not in payload
    assert "permission" not in payload
    assert "identity" not in payload
    assert "credential" not in payload


def test_semantic_protocol_rejects_invalid_contract_values() -> None:
    with pytest.raises(SemanticValidationError):
        validate_semantic_frame(SemanticFrame(speech_act="route_to_people", operation=Operation.COUNT.value))


def test_semantic_protocol_frame_is_immutable_runtime_object() -> None:
    frame = SemanticFrame()

    with pytest.raises(FrozenInstanceError):
        frame.operation = Operation.LIST.value  # type: ignore[misc]
