# Digital Advisor OS V5 Runtime Architecture

V5 is an enterprise AI operating system. Command Engine is now frozen on
Conversation First Command Engine Refactor V1. This is not an Intent Refactor:
Intent no longer owns first interpretation of natural language. Conversation
State, Semantic Understanding and Dialogue Resolver own the interpretation
path; Capability, Policy and Runtime are resolved after the CommandFrame.

Runtime code must follow this fixed layering:

```text
User Message
  -> ConversationState
  -> Semantic Understanding
  -> Dialogue Resolver
  -> CommandFrame
  -> Policy
  -> Runtime
  -> Response Orchestrator
  -> Interaction
```

## Layer contracts

### Interaction Layer

Allowed:

- Render cards, side panels, web views and push messages.
- Consume `RuntimeResult` or `InteractionPayload`.

Forbidden:

- Tool calls.
- Permission checks.
- Business planning.
- Cross-company context decisions.

### Command Layer

Allowed:

- Build `ConversationState` from current input and legacy conversation objects.
- Run Semantic Understanding. LLM output is limited to speech_act, topic,
  target, operation, requested_output, parameters, confidence and ambiguities.
- Run Dialogue Resolver.
- Output the sole Command Engine contract: `CommandFrame`.
- Build `CommandPlan`.
- Decide target UI.
- Recommend tool candidates.

Forbidden:

- Letting rules directly route natural language to a capability.
- Letting LLM output capability, provider, runtime, permission, identity or
  credential decisions.
- Adding `DialogueDecision`, `IntentFrame` or other competing Command outputs.
- Executing tools.
- Sending messages.
- Checking permissions.

Legacy objects such as `result_context`, `pending_action`,
`pending_confirmation` and clarification state remain available only as inputs
to `ConversationStateBuilder`. Entry modules must not consume them directly to
compete for route ownership.

### Policy & Context Layer

Allowed:

- Enforce tenant isolation.
- Bind `company_id`.
- Decide permission and confirmation requirements.
- Block global/multi-company access without explicit scope.
- Decide scope, permission, identity, credential, visibility, confirmation,
  authorization and result filters.

Forbidden:

- Understanding the user message.
- Tool execution.
- UI rendering.
- Business planning.

### Agent Runtime Core

Allowed:

- Create `RuntimeTask`.
- Execute planned steps.
- Drive task states: `pending -> running -> waiting -> done / failed`.
- Dispatch providers through the capability router.

Forbidden:

- Direct UI rendering.
- Provider self-orchestration.

### Tool Layer

Allowed:

- Provide passive capabilities.
- Execute one provider operation.

Forbidden:

- Calling other tools.
- Choosing UI.
- Planning workflows.
- Making permission decisions.

## Current implementation status

- `models.py` defines `CommandPlan`, `RuntimeTask`, `RuntimeStep`,
  `RuntimeResult` and `InteractionPayload`.
- `conversation_state.py`, `semantic_frame.py`, `conversation_hints.py` and
  `dialogue_resolver.py` define Conversation First V1.
- `command_layer.py` routes People and Knowledge through Conversation First V1
  and keeps other domains on the existing path for this migration stage.
- `policy_layer.py` enforces tenant boundary and delegates permission checks.
- `agent_runtime_core.py` is the single runtime execution entry.
- `runtime_result.py` normalizes runtime output.
- `interaction_layer.py` adapts runtime output for display.
- `runtime.py` main execution path now uses Command -> Policy -> Runtime Core.

## Migration rule

New capabilities must not be wired directly from Bot/Card/WebView to tools.
They must enter through Runtime:

```text
ConversationState -> SemanticFrame -> CommandFrame -> PolicyDecision -> RuntimeTask -> ProviderResult -> RuntimeResult
```

V1 scope:

- In scope: People, Knowledge.
- Out of scope: Task write actions, Approval write actions, Mail write actions,
  and full Provider migration.
- Any future entry bug must be attributed to one layer only:
  ConversationState, SemanticFrame, DialogueResolver, Policy, or Response
  Orchestrator. It must not be fixed by adding business-domain keyword routes.
