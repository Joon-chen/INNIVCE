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
Interface Layer
Observability Layer
Business Domains
```

主链路：

```text
Interaction
-> Command Engine
-> Policy Engine
-> Runtime Engine / Cognitive Engine
-> Provider / Tool
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
- 让 LLM 直接选择 Provider、Tool、API、credential 或执行身份。

Command LLM Intent V0 规则：

- 规则解析优先保护高置信动作，例如创建、发送、审批、完成任务。
- LLM 只能输出结构化 Intent Candidate，不得执行、不准越过 Validator。
- Validator 只能接受已登记 Runtime strategy、合法 question_type、合法 data_scope。
- 高置信 LLM 候选可补足泛化自然语言查询，例如企业任务负荷、公司日程风险。
- 低置信 LLM 候选只有在给出 missing_params / clarification 时，才能进入引导式对话。
- 未知 intent、低置信且不可追问候选、从 query 升级为 action 的候选必须丢弃。

Command Guided Clarification V0：

- 低置信但可追问的候选必须返回 clarification Result，不得执行 Provider。
- RuntimeResult / ResultContext 必须保留 `clarification_prompt`。
- 可选引导项进入 `clarification_options`，例如 scope 可给出 self / department / company，time_range 可给出 today / this_week / this_month。
- 引导选项只是用户输入建议，不代表 Policy 已授权，也不代表 Runtime 已执行。
- Interaction 可以渲染这些选项，但不能自己解释为业务状态或绕过 Command / Policy。

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

### 4.5 LLM Capability Architecture

LLM 不是一个单独大脑，也不是可以绕过系统边界的 Agent。V5 将 LLM 拆成五个受控能力位：

```text
Command LLM
Reasoning LLM
Conversation LLM
Presentation LLM
External Research Capability
```

统一链路：

```text
User Message
-> Conversation State
-> Command LLM / Rule Parser
-> Policy Preflight
-> Runtime / Provider / Cognitive Read
-> Policy Result Filter
-> Reasoning LLM
-> RuntimeResult
-> Presentation LLM
-> InteractionPayload
```

#### Command LLM

职责：

- 理解用户目标。
- 输出 intent、domain、capability、scope、对象、时间、约束、输出偏好。
- 发现缺参并生成 clarification。
- 作为 Command Enrichment 进入 RuntimeResult metadata。

禁止：

- 执行动作。
- 选择 Provider / Tool / API。
- 判断权限。
- 决定执行身份。

#### Reasoning LLM

职责：

- 基于 Policy 过滤后的 Operational Data / Evidence / Snapshot / Insight / MemoryCandidate 做分析。
- 生成 Summary、Risk、Recommendation、Decision Basis。
- 解释 AI 判断依据。

禁止：

- 读取未授权数据。
- 直接执行 Action。
- 用外部网页替代企业内部事实。

#### Conversation LLM

职责：

- 闲聊。
- 多轮澄清。
- 解释缺参、权限边界、授权需求和失败原因。
- 维护对话连续性。

禁止：

- 在闲聊中主动读取业务数据。
- 将闲聊意图升级为业务动作。
- 绕过 Command / Policy。

#### Presentation LLM

职责：

- 输出侧表达优化。
- 应用用户画像、角色、语气、详细程度、格式偏好。
- 保持数量、状态、权限边界和业务结论不变。

禁止：

- 改变 Result 数量、状态、风险等级或建议含义。
- 影响权限、Provider、Runtime 执行、Action 确认。
- 把 Profile 写入 Command / Policy / Runtime 控制面。

#### External Research Capability

External Research 不是自由浏览器。它是受 Policy 管控的外部信息补充能力。

归属：

- Command LLM 判断是否需要 `external_research`。
- Policy Engine 判断是否允许联网、允许哪些来源、哪些业务域可用。
- Runtime Engine 统一执行。
- Web Provider 负责检索和读取公开网页。
- Reasoning LLM 基于外部来源与企业内部可见上下文分析。
- Presentation LLM 负责引用来源和表达。

必须支持开关：

- 系统级：`external_research_enabled`。
- 公司级：公司是否允许联网。
- 业务域级：哪些 Domain 可联网。
- 能力级：哪些 Capability 可联网。
- 来源级：白名单 / 黑名单。

RuntimeResult 必须标记：

```text
external_source_used
sources
retrieved_at
confidence
policy_decision
```

External Research V0 合同：

```json
{
  "ExternalResearchPolicy": {
    "enabled": false,
    "company_id": "",
    "allowed_domains": [],
    "allowed_capabilities": [],
    "allowed_sources": [],
    "blocked_sources": [],
    "requires_citation": true,
    "max_source_age_days": null
  },
  "ExternalResearchRequest": {
    "company_id": "",
    "domain": "",
    "capability": "",
    "query": "",
    "purpose": "supplement_internal_context",
    "source_constraints": [],
    "requested_at": ""
  },
  "ExternalResearchResult": {
    "status": "success/failed/blocked",
    "summary": "",
    "sources": [],
    "retrieved_at": "",
    "confidence": "low/medium/high",
    "policy_decision": {}
  },
  "ExternalSourceReference": {
    "title": "",
    "url": "",
    "source_type": "public_web/official_doc/news/other",
    "published_at": "",
    "retrieved_at": "",
    "excerpt": ""
  }
}
```

合同边界：

- `ExternalResearchPolicy` 由 Policy Engine 生成，不由 LLM 生成。
- `ExternalResearchRequest` 由 Command Layer 生成候选，必须经过 Policy Preflight 才能执行。
- `ExternalResearchResult` 只能作为 Reasoning LLM 的补充输入，不得覆盖企业内部事实。
- `ExternalSourceReference` 必须进入 RuntimeResult metadata，供 Presentation LLM 引用。

联网禁止：

- 绕过 Policy。
- 替代企业内部实时数据。
- 用当前用户 USER_TOKEN 做外部研究越权。
- 无来源引用地输出外部事实。

## 5. Interface Layer

Interface Layer 包括：

```text
Interaction Layer
```

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

### 5.1 Provider / Tool Boundary

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

## 7. Business Domains

Business Domains 是企业工作分类，不是飞书产品模块，也不是 Runtime 模块。

冻结 Domain：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

映射规则：

- People：组织、人员、通讯录、考勤、绩效、招聘、薪资。
- Communication：消息、群聊、邮件、公告、机器人。
- Workspace：任务、待办、OKR、项目、日程、会议。
- Process：审批、报销、采购、付款、请假、出差。
- Knowledge：知识库、Wiki、文档、文件、表格、纪要。
- Business：客户、商机、订单、合同、供应商、产品、工单、库存。
- Intelligence：日报、周报、总结、风险分析、经营分析、管理洞察。

飞书功能只能映射为 Provider 或 Provider Binding。

## 8. Standard Contracts

### 8.1 RuntimeActionInput

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

### 8.2 RuntimeResult

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

### 8.3 WorkEvent

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

## 9. Architecture Index

| Document | Status | Role |
| --- | --- | --- |
| `V5_RUNTIME_CONSTITUTION.md` | ACTIVE | 唯一架构总图 |
| `CAPABILITY_REGISTRY_MODEL.md` | ACTIVE | Foundation: Domain -> Capability -> Skill -> Provider |
| `ENTERPRISE_COGNITIVE_FOUNDATION_V1.md` | ACTIVE | Cognitive Engine V1 |
| `UNIFIED_POLICY_ENGINE_V0_DESIGN.md` | ACTIVE | Policy Engine V0 |
| `CURRENT_MISSION.md` | ACTIVE | 当前任务 |
| `ENTERPRISE_SCOPE_MODEL_REVIEW.md` | FROZEN | Scope Model 评审记录 |
| `EXECUTION_IDENTITY_AUDIT.md` | FROZEN | BOT / USER / TENANT / CLI 身份审计 |
| `EXECUTION_IDENTITY_CONTRACT_DESIGN.md` | FROZEN | 执行身份合同设计 |
| `TASK_RUNTIME_SAMPLE_*` | FROZEN | Task Runtime 样板记录 |
| `APPROVAL_RUNTIME_SAMPLE.md` | FROZEN | Approval Runtime 样板 |
| `APPROVAL_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md` | FROZEN | Approval 样板验收记录 |
| `ECF_V1_FREEZE_REVIEW.md` | FROZEN | Cognitive V1 冻结评审 |
| `SNAPSHOT_TRIGGER_MATRIX.md` | FROZEN | Snapshot 生成节奏规则 |
| `REGISTRY_*` / `CAPABILITY_REGISTRY_*` review docs | FROZEN | Registry 设计和迁移评审记录 |
| `ENTERPRISE_AI_OS_V1.md` | ARCHIVED | 旧企业 AI OS 草案，已被本文吸收 |
| `V5_RUNTIME_ARCHITECTURE.md` | ARCHIVED | 旧 Runtime 架构草案，已被本文吸收 |
| `V5_CAPABILITY_TAXONOMY.md` | ARCHIVED | 旧能力域分类草案，已被 Capability Registry 吸收 |
| `docs/history/*` | ARCHIVED | 历史阶段归档 |

## 10. Documentation Archive List

以下文档只作为历史或样板参考，不再作为最高级架构入口：

- `docs/history/*`
- `ENTERPRISE_AI_OS_V1.md`
- `V5_RUNTIME_ARCHITECTURE.md`
- `V5_CAPABILITY_TAXONOMY.md`
- `CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`
- `CAPABILITY_REGISTRY_READ_API_REVIEW.md`
- `CAPABILITY_REGISTRY_SHADOW_VERIFICATION.md`
- `REGISTRY_FALLBACK_DEPRECATION_REVIEW.md`
- `REGISTRY_FALLBACK_OBSERVATION.md`
- `REGISTRY_UI_FREEZE_REVIEW.md`
- `SKILL_REGISTRY_CLEANUP_PLAN.md`
- `TASK_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md`
- `TASK_RUNTIME_SAMPLE_CONTRACT_REVIEW.md`
- `TASK_RUNTIME_SAMPLE_FEISHU_MANUAL_ACCEPTANCE.md`
- `TASK_COGNITIVE_SAMPLE_DESIGN.md`
- `SECOND_BUSINESS_SAMPLE_SELECTION.md`
- `APPROVAL_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md`
- `APPROVAL_INSIGHT_SAMPLE.md`
- `INTERACTION_RENDERER_GENERALIZATION_AUDIT.md`
- `ECF_V1_FREEZE_REVIEW.md`
- `SNAPSHOT_TRIGGER_MATRIX.md`
- `INSIGHT_CONTRACT_V0.md`
- `INSIGHT_RENDERER_BOUNDARY.md`
- `OA_INTELLIGENCE_LOOP_DESIGN.md`
- `USER_RESOLUTION_AUDIT.md`
- `PAGE_RESPONSIBILITY_AUDIT.md`

若这些文档与本文冲突，以本文为准。

## 11. Duplicate Concept Cleanup

| Duplicate / Legacy Concept | Canonical Concept |
| --- | --- |
| Command Layer | Command Engine |
| Policy Layer / Policy & Context Layer | Policy Engine |
| Agent Runtime / Runtime Layer | Runtime Engine |
| Memory / WorkEvent / Snapshot scattered docs | Cognitive Engine |
| Cognitive Foundation | Cognitive Engine |
| Profile / Style / Preference as separate subsystem | Cognitive Engine |
| Tool Layer / Provider Layer as architecture layer | Provider / Tool Boundary |
| Card Policy | Interaction render-only contract |
| Diagnostics as business governance | Observability Layer |
| Feishu product modules | Business Domain + Provider Binding |
| Approval / Task as top-level module | Process / Workspace capabilities |
| Capability Catalog / Skill Registry as runtime pages | Capability Registry consumers |
| LLM agent directly choosing tools | Command LLM Intent Candidate + Validator |

## 12. Freeze Rule

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
