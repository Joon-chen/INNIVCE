from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal
from uuid import UUID


QuestionType = Literal["query", "analysis", "insight", "decision", "action"]
DataScope = Literal["self", "person", "department", "company", "project", "organization", "external"]
ExecutionIdentity = Literal["bot", "user"]
RuntimeScopeType = Literal["single_company", "multi_company", "all_companies"]
ProviderStatus = Literal["success", "partial", "denied", "error", "skipped"]
RuntimeTaskStatus = Literal["pending", "running", "waiting", "done", "failed"]
RuntimeActionStatus = Literal["pending", "waiting_input", "waiting_confirmation", "confirmed", "executing", "done", "failed", "cancelled", "stale"]
RuntimeActionType = Literal["approve", "reject", "transfer", "add_sign", "confirm", "cancel", "open_detail", "execute"]
RuntimeActionSourceUI = Literal["portal", "card", "sidepanel", "webview", "bot", "unknown"]
TargetUI = Literal["card", "sidepanel", "webview", "push", "ios", "none"]


@dataclass(frozen=True)
class RuntimeIdentity:
    user_id: str = ""
    open_id: str = ""
    role: str = ""
    display_name: str = ""
    department_id: str = ""
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeScope:
    scope_type: RuntimeScopeType = "single_company"
    company_ids: tuple[UUID, ...] = ()
    active_company_id: UUID | None = None
    active_department_id: str = ""
    active_project_id: str = ""

    def for_company(self, company_id: UUID) -> "RuntimeScope":
        return replace(
            self,
            scope_type="single_company",
            company_ids=(company_id,),
            active_company_id=company_id,
        )


@dataclass(frozen=True)
class RuntimeProfile:
    style: str = "professional"
    verbosity: str = "balanced"
    use_formatting: bool = True
    tone_tips: str = ""


@dataclass(frozen=True)
class ResultContext:
    result_type: str = ""
    query_id: str = ""
    count: int = 0
    items: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    answer: str = ""

    @property
    def has_items(self) -> bool:
        return bool(self.items)


@dataclass(frozen=True)
class RuntimeContext:
    identity: RuntimeIdentity
    runtime_scope: RuntimeScope
    current_message: str
    session_context: dict[str, Any] = field(default_factory=dict)
    profile: RuntimeProfile = field(default_factory=RuntimeProfile)
    result_context: ResultContext | None = None
    chat_id: str | None = None

    def for_company(self, company_id: UUID) -> "RuntimeContext":
        return replace(self, runtime_scope=self.runtime_scope.for_company(company_id))


@dataclass(frozen=True)
class ResultFollowup:
    is_result_followup: bool
    followup_type: str = ""
    entity_ref: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentResult:
    question_type: QuestionType
    intent: str
    data_scope: DataScope
    entities: dict[str, Any] = field(default_factory=dict)
    missing_params: tuple[str, ...] = ()
    confidence: float = 0.0
    canonical_question: str = ""

    @property
    def needs_clarification(self) -> bool:
        return self.confidence < 0.6 or bool(self.missing_params)


@dataclass(frozen=True)
class PlannerResult:
    strategy: str
    sources: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandPlan:
    """Command Layer output.

    This is the V5 frozen contract. It decides intent, execution steps,
    target UI and candidate tools, but does not execute anything.
    """

    intent: str
    steps: tuple[dict[str, Any], ...]
    target_ui: TargetUI
    tool_candidates: tuple[str, ...]
    context_scope: dict[str, Any]
    intent_result: IntentResult
    planner_result: PlannerResult


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False
    execution_identity: ExecutionIdentity = "bot"
    per_company: dict[str, "PermissionDecision"] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRequest:
    source: str
    operation: str
    intent: IntentResult
    planner: PlannerResult
    context: RuntimeContext
    execution_identity: ExecutionIdentity
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResult:
    source: str
    status: ProviderStatus
    result_type: str = ""
    count: int = 0
    items: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    error: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    strategy: str
    status: ProviderStatus
    provider_results: tuple[ProviderResult, ...]
    result_context: ResultContext | None = None


@dataclass(frozen=True)
class RuntimeStep:
    source: str
    operation: str
    status: RuntimeTaskStatus = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeTask:
    task_id: str
    intent: str
    strategy: str
    status: RuntimeTaskStatus = "pending"
    steps: tuple[RuntimeStep, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeActionState:
    action_id: str
    task_id: str
    status: RuntimeActionStatus
    intent: str
    strategy: str
    message: str = ""
    sources: tuple[str, ...] = ()
    confirmation_token: str = ""
    created_at: str = ""
    updated_at: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeTaskState:
    task_id: str
    status: RuntimeTaskStatus
    intent: str
    strategy: str
    actions: tuple[RuntimeActionState, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeActionConfirmation:
    confirmed: bool = False
    token: str = ""


@dataclass(frozen=True)
class RuntimeActionContext:
    company_id: str = ""
    chat_id: str = ""
    user_id: str = ""
    open_id: str = ""
    source_ui: RuntimeActionSourceUI = "unknown"


@dataclass(frozen=True)
class RuntimeActionInput:
    """Frozen action input contract for UI entrypoints.

    Portal, Card, SidePanel and WebView should adapt to this shape before
    Runtime turns the action into state transitions.
    """

    action_id: str
    action_type: RuntimeActionType
    intent: str
    strategy: str
    target: dict[str, Any] = field(default_factory=dict)
    confirmation: RuntimeActionConfirmation = field(default_factory=RuntimeActionConfirmation)
    context: RuntimeActionContext = field(default_factory=RuntimeActionContext)
    message: str = ""
    sources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimePendingAction:
    """Session-backed pending action contract used by Runtime state."""

    action_id: str
    message: str
    intent: str
    intent_label: str
    strategy: str
    sources: tuple[str, ...]
    company_id: str
    question_type: str = "action"
    data_scope: str = "self"
    entities: dict[str, Any] = field(default_factory=dict)
    route_path: str = ""
    execution_identity: str = "user"
    requires_confirmation: bool = True
    confirmation_token: str = ""
    source: str = "runtime_action_input"
    runtime_action_input: dict[str, Any] = field(default_factory=dict)
    missing_params: tuple[str, ...] = ()
    input_contract: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeResult:
    result_type: str
    status: str
    title: str
    summary: str
    items: tuple[dict[str, Any], ...] = ()
    actions: tuple[dict[str, Any], ...] = ()
    target_ui: TargetUI = "card"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InteractionPayload:
    """Interaction Layer input.

    Renderers consume this structure. They should not call tools or make
    business decisions.
    """

    payload_type: str
    title: str
    summary: str
    recommendation: str = ""
    status: str = ""
    items: tuple[dict[str, Any], ...] = ()
    actions: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComposedAnswer:
    answer: str
    result_context: ResultContext | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerEnvelope:
    context: RuntimeContext
    intent: IntentResult
    plan: PlannerResult
    permission: PermissionDecision
    execution: ExecutionResult | None
    composed: ComposedAnswer
