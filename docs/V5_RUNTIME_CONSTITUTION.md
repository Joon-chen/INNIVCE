# V5 Runtime Constitution

本文档是 Digital Advisor OS V5 的唯一架构总图。新成员只看本文，应能理解系统的 Foundation、Core Engines、Interface、Observability 与 Business Domains 五层结构。

本文档更新频率应很低，只在长期架构边界变化时修改。

## 1. System Goal

Digital Advisor 不是一组飞书 API、卡片逻辑或后台调试接口的集合，而是面向企业工作的 AI OS。

核心目标：

```text
用企业 OA 原始数据沉淀企业认知，
再通过统一 Policy 给不同角色输出合适的 Operational Data、Evidence、Snapshot、Insight 与 Action。
```

优先级：

1. 先稳架构边界。
2. 再扩业务样板。
3. 最后打磨体验。

## 2. V5 Five-Layer Architecture

V5 冻结为五层：

```text
Foundation Layer
Core Engines
Interface Layers
Provider Layer
Observability Layer
```

主链路：

```text
Interaction
-> Command Engine
-> Policy Engine
-> Runtime Engine / Cognitive Engine
-> Provider Layer
-> Policy Result Filter
-> RuntimeResult / InteractionPayload
-> Interaction
```

统一原则：

```text
Interaction 只展示和收集输入
Command 只理解和规划
Policy 只管边界、身份、授权、裁剪
Runtime 只管状态和执行
Cognitive 只管事实、证据、快照、洞察
Provider 只做被动外部能力
Observability 只诊断、审计、观测
```

## 3. Foundation Layer

Foundation Layer 是系统元数据与上下文底座，不执行业务。

包含：

```text
Business Domain Taxonomy
Capability Registry
Skill Registry
Provider Binding
Identity & Scope Model
Context Contract
```

### 3.1 Business Domains

Business Domain 已冻结为：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

规则：

- 不按飞书产品建 Domain。
- Approval 归 Process。
- Task / Calendar / Meeting / OKR 归 Workspace。
- Bitable / Base 只是 Business 数据载体，不是 Domain。
- Domain 是 Capability Registry 的根。

### 3.2 Capability Registry

统一模型：

```text
Business Domain
-> Capability
-> Skill
-> Provider Binding
```

职责：

- Domain 回答：业务世界怎么分类。
- Capability 回答：业务用户能让数字参谋做什么。
- Skill 回答：系统内部有哪些可治理、可授权、可测试、可审计的原子能力。
- Provider Binding 回答：具体由哪个外部或内部能力实现。

Capability Registry 不是 Observability，也不是 Runtime。它是 System Metadata Foundation，并被以下模块共同使用：

- Command Engine：识别 capability 候选。
- Policy Engine：读取风险、确认、授权要求。
- Runtime Engine：选择 execution strategy 与 provider operation。
- Interaction Layer：展示能力目录、能力清册、治理中心。
- Observability Layer：做缺口诊断和健康检查。

### 3.3 Identity & Scope

企业级 Scope Model：

```text
SELF
USER
TEAM
DEPARTMENT
COMPANY
```

所有 Runtime Context、RuntimeActionInput、RuntimeResult、ResultContext 必须携带 company_id。禁止隐式默认公司。

## 4. Core Engines

Core Engines 是系统核心，不直接绑定 UI 或飞书产品。

```text
Command Engine
Policy Engine
Runtime Engine
Cognitive Engine
```

### 4.1 Command Engine

职责：

- 理解用户输入。
- 识别 intent、domain、capability candidate、scope。
- 提取参数。
- 判断缺参。
- 决定 target_ui。
- 输出结构化 CommandPlan。

允许使用 LLM，但 LLM 只能输出结构化候选，不能直接执行。

冻结链路：

```text
User Message
-> LLM / Rule Command Parser
-> Structured Intent Candidate
-> Command Validator
-> CommandPlan
-> Policy Engine
```

Command Engine 禁止：

- 做权限判断。
- 调用 Tool / Provider / Feishu API / MCP / CLI。
- 写 WorkEvent。
- 生成最终业务 Result。
- 绕过 Policy 或 Runtime。

标准 Plan 最小结构：

```json
{
  "intent": "",
  "steps": [],
  "target_ui": "",
  "tool_candidates": [],
  "context_scope": {
    "company_id": "",
    "mode": "single_company"
  }
}
```

### 4.2 Policy Engine

Policy Engine 独立于 Runtime，但 V0 可作为同仓同进程 service 模块实现。

职责：

- Operational Data 权限。
- Cognitive Data 权限。
- Scope 判断。
- Identity Decision。
- USER fallback 允许条件。
- 高风险确认策略。
- Policy Preflight。
- Policy Result Filter。

统一链路：

```text
CommandPlan
-> Policy Preflight
-> Runtime / Cognitive Read
-> Policy Result Filter
-> RuntimeResult
```

核心原则：

- 最终结果权限 = 实时数据权限 ∩ 认知数据权限 ∩ 当前查询 scope。
- 认知层不能比来源对象更开放。
- USER_TOKEN 不能成为认知层越权依据。
- COMPANY / DEPARTMENT / USER 查询不能偷偷使用当前用户 USER_TOKEN 代查。
- USER_TOKEN 只用于当前用户个人资源 fallback 或代表当前用户执行动作。

### 4.3 Runtime Engine

Runtime Engine 是唯一执行入口。

禁止：

```text
Bot handler -> Tool
Card action -> Tool
WebView -> Tool
Provider -> Tool
```

必须收敛为：

```text
Bot / Card / WebView
-> Runtime Action Endpoint
-> Runtime State Machine
-> Provider / Tool
```

Runtime 管理：

- RuntimeTaskState。
- RuntimeActionState。
- RuntimeActionInput。
- Waiting Input。
- Waiting Confirmation。
- Executing。
- Done / Failed / Blocked。
- RuntimeResult。
- 执行日志和错误恢复。

Runtime 不负责：

- 业务域分类。
- 长期认知沉淀。
- 权限规则定义。
- UI 渲染。

### 4.4 Cognitive Engine

Cognitive Engine 负责把 Operational Data 转为企业认知。

冻结链路：

```text
Operational Data
-> WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

职责：

- WorkEvent：事实层，记录发生了什么。
- Evidence：判断依据层，保存结构化证据与指标。
- Snapshot：当前认知层，保存对象或主体的当前状态。
- Insight：建议层，输出 Recommendation，不执行动作。
- Profile / Style / Preference：用户风格、角色画像、偏好快照，属于 Cognitive Engine。

Cognitive Engine 禁止：

- 直接执行 Action。
- 绕过 Policy 暴露明细。
- 把 WorkEvent 当实时 Operational Data。
- 构建第二套 Runtime。

Action 仍归 Runtime：

```text
Insight
-> Runtime Action
-> Provider
```

## 5. Interface Layers

Interface Layers 包括：

```text
Interaction Layer
Provider Layer
```

### 5.1 Interaction Layer

包含：

- Bot Card。
- SidePanel。
- WebView / Portal。
- Push。
- iOS App。

职责：

- 展示 RuntimeResult / InteractionPayload。
- 收集用户输入。
- 将动作转换为 RuntimeActionInput。

禁止：

- 调用 Tool / Provider / Feishu API / MCP / CLI。
- 判断权限。
- 自己生成业务 Result。
- 自己解释业务状态。
- 查询数据库补业务状态。

### 5.2 Provider Layer

Provider / Tool 只做被动能力：

- 查询外部系统。
- 执行外部动作。
- 返回原始结果或 ProviderResult。

Provider 禁止：

- 决定业务规划。
- 决定 UI。
- 决定高风险确认。
- 决定认知层展示权限。

## 6. Observability Layer

Observability 旁路观察全部，不参与业务执行。

包含：

- Diagnostics。
- Audit。
- Telemetry。
- Runtime Diagnostics。
- Provider Health。
- Registry Health。

Observability 可以读取 Capability Registry 做健康检查，但 Capability Registry 不属于 Observability。

## 7. Standard Contracts

### 7.1 RuntimeActionInput

所有 Card / Portal / SidePanel / WebView action 必须转换为 RuntimeActionInput 后进入 Runtime。

必须携带：

```text
company_id
chat_id / session_id
user_id / open_id
source_ui
action_id
result_id / task_id
confirmation token when needed
```

### 7.2 RuntimeResult

Runtime 输出给交互层的标准 Result：

```json
{
  "result_type": "summary/action/feedback/delivery",
  "status": "success/failed/waiting",
  "title": "",
  "summary": "",
  "items": [],
  "actions": [],
  "target_ui": "",
  "metadata": {}
}
```

InteractionPayload 只能由 InteractionPayload Builder 从 RuntimeResult 转换而来。

### 7.3 WorkEvent

WorkEvent 是 append-only 事实层，必须携带 company_id。

标准字段：

```text
id
company_id
event_type
object_type
object_id
source
actor
payload
created_at
```

## 8. Architecture Index

| Document | Status | Role |
| --- | --- | --- |
| `V5_RUNTIME_CONSTITUTION.md` | ACTIVE | 唯一架构总图 |
| `CAPABILITY_REGISTRY_MODEL.md` | ACTIVE | Foundation: Domain -> Capability -> Skill -> Provider |
| `ENTERPRISE_COGNITIVE_FOUNDATION_V1.md` | ACTIVE | Cognitive Engine V1 |
| `UNIFIED_POLICY_ENGINE_V0_DESIGN.md` | ACTIVE | Policy Engine V0 |
| `CURRENT_MISSION.md` | ACTIVE | 当前任务 |
| `ENTERPRISE_SCOPE_MODEL_REVIEW.md` | FROZEN | Scope Model 评审记录 |
| `TASK_ENTERPRISE_PROVIDER_FEASIBILITY_AUDIT.md` | FROZEN | Task 企业读取可行性审计 |
| `TASK_RUNTIME_SAMPLE_*` | FROZEN | Task Runtime 样板历史 |
| `APPROVAL_RUNTIME_SAMPLE.md` | FROZEN | Approval Runtime 样板 |
| `ECF_V1_FREEZE_REVIEW.md` | FROZEN | Cognitive V1 冻结评审 |
| `docs/history/*` | ARCHIVED | 历史阶段归档 |

## 9. Documentation Archive List

以下文档只作为历史或样板参考，不再作为最高级架构入口：

- `docs/history/*`
- `TASK_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md`
- `TASK_RUNTIME_SAMPLE_CONTRACT_REVIEW.md`
- `TASK_RUNTIME_SAMPLE_FEISHU_MANUAL_ACCEPTANCE.md`
- `TASK_COGNITIVE_SAMPLE_DESIGN.md`
- `SECOND_BUSINESS_SAMPLE_SELECTION.md`
- `ECF_V1_FREEZE_REVIEW.md`
- `WORK_EVENT_V0_DESIGN.md`
- `SNAPSHOT_TRIGGER_MATRIX.md`

若这些文档与本文冲突，以本文为准。

## 10. Duplicate Concept Cleanup

| Duplicate / Legacy Concept | Canonical Concept |
| --- | --- |
| Command Layer | Command Engine |
| Policy Layer / Policy & Context Layer | Policy Engine |
| Agent Runtime / Runtime Layer | Runtime Engine |
| Memory / WorkEvent / Snapshot scattered docs | Cognitive Engine |
| Tool Layer | Provider Layer |
| Card Policy | Interaction render-only contract |
| Diagnostics as business governance | Observability Layer |
| Feishu product modules | Business Domain + Provider Binding |
| Approval / Task as top-level module | Process / Workspace capabilities |

## 11. Freeze Rule

禁止在未更新本文的情况下新增：

- Engine。
- Foundation。
- Runtime Layer。
- Architecture V2。
- Policy V2。

新增业务样板必须挂到：

```text
Business Domain
-> Capability
-> Skill
-> Provider Binding
```

新增认知能力必须挂到：

```text
WorkEvent
-> Evidence
-> Snapshot
-> Insight
```
