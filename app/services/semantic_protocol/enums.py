from __future__ import annotations

from enum import StrEnum


class SpeechAct(StrEnum):
    ASK = "ask"
    FOLLOWUP = "followup"
    REQUEST_ACTION = "request_action"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    ANSWER = "answer"


class Operation(StrEnum):
    ASK = "ask"
    COUNT = "count"
    LIST = "list"
    FIELD_LOOKUP = "field_lookup"
    EXISTS = "exists"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    COMPANY_PROFILE = "company_profile"
    KNOWLEDGE_QUERY = "knowledge_query"
    ACTION_REQUEST = "action_request"
    FOLLOWUP = "followup"
    SEND_MESSAGE = "send_message"


class TargetType(StrEnum):
    UNKNOWN = "unknown"
    SELF = "self"
    PERSON = "person"
    ORGANIZATION_UNIT = "organization_unit"
    COLLECTION = "collection"
    FIELD = "field"
    PREVIOUS_RESULT = "previous_result"
    CHAT = "chat"


class OutputMode(StrEnum):
    NATURAL_TEXT = "natural_text"
    NUMERIC_ONLY = "numeric_only"
    SHORT_ANSWER = "short_answer"
    COUNT = "count"
    NAME_ONLY = "name_only"
    FULL_LIST = "full_list"
    DETAIL = "detail"
    SIDEPANEL = "sidepanel"


class PresentationMode(StrEnum):
    TEXT = "text"
    CARD = "card"
    SIDEPANEL = "sidepanel"
    WEBVIEW = "webview"


class ActionType(StrEnum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    SEND = "send"
    DRAFT = "draft"


class Tone(StrEnum):
    NATURAL = "natural"
    CONCISE = "concise"
    FORMAL = "formal"
