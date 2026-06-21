# Digital Advisor OS V5 Upgrade Plan

## Baseline

Runtime V5 follows the Runtime Constitution as the source of truth:

Feishu Message -> Pre Gateway -> Result Follow-up Detector -> Intent Recognition -> Task Planner -> Permission Check -> Capability Router -> Execution -> Answer Composer -> Smart Reply / Card.

The system is centered on question, data scope, knowledge source, and execution strategy. It is not centered on tools, skills, or database tables.

## Current Scope

In scope now:

- Feishu enterprise resources
- Company profile
- Knowledge base
- WorkEvent
- Memory
- Runtime profile
- Web search
- Feishu native skills where useful

Out of scope for now:

- WeChat
- DingTalk
- External personal mailboxes
- iOS App UI
- Management admin UI

The future iOS App and admin UI should call the same Runtime V5. They are clients, not separate runtimes.

## Multi-company Model

Runtime V5 supports single-company, multi-company, and all-companies scopes at the data model level.

The Feishu bot entry point defaults to single-company. The future iOS App can pass all-companies scope and the Capability Router can fan out provider calls per company, then merge results with company metadata.

## Migration Rules

- Pre Gateway may load identity, session, result context, profile, and rewrite incomplete queries.
- Pre Gateway must not call skills, tools, or providers.
- Result Follow-up Detector only detects follow-up type. It must not answer.
- Intent Recognition outputs question type, intent, data scope, entities, missing params, and confidence.
- Task Planner outputs strategy and sources. It must not choose functions.
- Capability Router executes sources in planner order through Resource Providers.
- Resource Providers may use Feishu Skill, OpenAPI, MCP, or CLI internally.
- Answer Composer organizes facts and recommendations. It must not invent facts.

## First Implementation Target

The first executable slice should support:

- `people_lookup`
- `approval_query`
- `organization_export`
- structured result follow-up

Acceptance examples:

- `总经理是谁`
- `待我审批有哪些`
- `帮我创建一个表并把组织架构放进去，把文件发给我`
- `他们是谁`
- `第一个是谁`

## Code Landed

Initial V5 runtime package:

- `app/services/runtime_v5/models.py`
- `app/services/runtime_v5/context.py`
- `app/services/runtime_v5/result_followup.py`
- `app/services/runtime_v5/intent.py`
- `app/services/runtime_v5/planner.py`
- `app/services/runtime_v5/permission.py`
- `app/services/runtime_v5/capability_router.py`
- `app/services/runtime_v5/composer.py`
- `app/services/runtime_v5/runtime.py`

The first Feishu adapters are also available:

- People Provider
- Approval Provider
- Base Provider
- Message Provider

These providers expose Resource Provider operations externally and reuse existing Tool Router / MCP execution internally during migration.

Current executable behavior:

- `approval_query` reads pending approval tasks through Approval Provider.
- `people_lookup` and `organization_snapshot` read Feishu contacts through People Provider.
- `organization_export` executes `people -> base`.
- When an existing `bascn-*` is provided, Base Provider writes into that Base.
- When no `bascn-*` is provided and the user asks to create a table, Base Provider creates a new Base file, creates/uses the organization table, and writes rows.
- The normal bot reply sends the created Base information back to the user. Message Provider is reserved for explicit message-sending actions, not ordinary bot replies.
- Chat-session actions requiring writes are intercepted by Runtime V5 confirmation before execution.

Confirmation behavior:

- High-risk actions are saved as a pending runtime action in session context.
- The user must reply `确认执行` to resume and execute the original action.
- The user can reply `取消` to drop the pending action.
- The Feishu bot trace exposes `requires_confirmation`, `execution_identity`, and `execution_status`.
- Confirmation prompts use route path `runtime_v5_confirmation` so they are not rendered as business data cards.

## Runtime Switch

The Feishu bot entry can be switched to V5 with:

- `FEISHU_BOT_RUNTIME_V5_ENABLED=true`

The default remains the existing runtime while V5 is being migrated and verified.
