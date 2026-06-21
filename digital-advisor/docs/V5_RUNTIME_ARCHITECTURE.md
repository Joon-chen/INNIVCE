# Digital Advisor OS V5 Runtime Architecture

V5 is an enterprise AI operating system. Runtime code must follow this fixed
layering:

```text
User Input
  -> Command Layer
  -> Policy & Context Layer
  -> Agent Runtime Core
  -> Tool Layer
  -> RuntimeResult
  -> Interaction Layer
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

- Parse user intent.
- Build `CommandPlan`.
- Decide target UI.
- Recommend tool candidates.

Forbidden:

- Executing tools.
- Sending messages.
- Checking permissions.

### Policy & Context Layer

Allowed:

- Enforce tenant isolation.
- Bind `company_id`.
- Decide permission and confirmation requirements.
- Block global/multi-company access without explicit scope.

Forbidden:

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
- `command_layer.py` builds the V5 command plan.
- `policy_layer.py` enforces tenant boundary and delegates permission checks.
- `agent_runtime_core.py` is the single runtime execution entry.
- `runtime_result.py` normalizes runtime output.
- `interaction_layer.py` adapts runtime output for display.
- `runtime.py` main execution path now uses Command -> Policy -> Runtime Core.

## Migration rule

New capabilities must not be wired directly from Bot/Card/WebView to tools.
They must enter through Runtime:

```text
CommandPlan -> PolicyDecision -> RuntimeTask -> ProviderResult -> RuntimeResult
```
