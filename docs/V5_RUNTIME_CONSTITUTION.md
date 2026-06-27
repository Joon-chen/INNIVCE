# V5 Runtime Constitution

本文档是 Digital Advisor OS V5 的唯一架构总图。新成员只看本文，应能理解系统的 Foundation、Core Engines、Interface、Observability 与 Business Domains 五层结构。

本文档更新频率应很低，只在长期架构边界变化时修改。

阅读规则：

- 先看本文，理解系统是什么。
- 再看 ACTIVE 文档，理解仍在生效的专项合同。
- FROZEN 文档只作为样板、审计或阶段结论参考。
- ARCHIVED 文档只保留历史，不再作为设计依据。
- 若任何文档与本文冲突，以本文为准。

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

统一原则：

```text
Interaction 只展示和收集输入
Command 只管理对话理解、语义帧、对话裁决和 CommandFrame
Policy 只管边界、身份、授权、裁剪
Runtime 只管状态和执行
Cognitive 只管事实、证据、快照、洞察
Provider 只做被动外部能力
Observability 只诊断、审计、观测
```

### 2.1 Organization Foundation & Unified Policy Freeze

以下原则冻结为 AI OS V5 Foundation 级架构约束：

```text
There is only one permission system in AI OS.

Organization Foundation defines organizational truth.

Policy Engine is the only component allowed to make permission decisions.

Operational Filter and Cognitive Filter are both Policy Engine filters.

Runtime, Cognitive Engine and LLM must only consume policy-filtered data.

No component is allowed to bypass Policy.
```

边界规则：

- Organization Foundation 属于 Foundation Layer，负责组织事实、身份关系、组织范围、管理范围和组织对象解析。
- Policy Engine 是唯一权限系统，负责 Permission、Scope、Identity、Visibility、Confirmation、Authorization 和 Result Filter。
- Operational Filter 与 Cognitive Filter 不是两套权限系统，只是 Policy Engine 面向不同资源平面的两个过滤器。
- Runtime 禁止直接查询飞书通讯录或自行解析组织字符串。
- Cognitive Engine 只负责认知对象的存储、索引和召回，不负责权限决策。
- LLM 永远不能读取未经 Policy Filter 的 Operational 或 Cognitive 数据。

### 2.2 Architecture Convergence Phase

从 V5 Freeze 起，AI OS 进入 Architecture Convergence Phase。

目标：

```text
扩业务，少扩架构。
```

新增问题必须先回答：

```text
这个问题属于哪个已有模块？
```

而不是：

```text
需要新增什么模块？
```

当前 V5 Architecture 冻结为：

```text
Foundation
- Business Domain Taxonomy
- Capability Registry
- Skill Registry
- Provider Registry
- Identity & Scope
- Context Store
- Organization Foundation

Core Engines
- Command Engine
- Policy Engine
- Runtime Engine
- Cognitive Engine

Interface
- Interaction Layer
- Provider Layer

Observability
- Diagnostics
- Audit
- Telemetry
```

归类规则：

- People 查询错误属于 Command Engine、ConversationState、SemanticFrame、DialogueResolver、Organization Resolver。
- 权限问题属于 Policy Engine。
- 组织关系属于 Organization Foundation。
- LLM 表达属于 Response Orchestrator。
- Provider 调用问题属于 Provider Layer。

新增系统模块只有在同时满足以下条件时才允许：

- 已有模块无法承担职责。
- 职责具有长期稳定性。
- 至少两个以上业务域都会依赖。

否则必须放回已有模块。

禁止事项：

- 发现一个问题就新增 Engine。
- 发现一个问题就新增 Foundation。
- 发现一个问题就新增 Pipeline。
- 发现一个问题就新增 Contract。

Foundation 保存事实，不保存业务逻辑；Engine 做决策，不保存组织事实；Provider 只调用能力，不理解用户、不判断权限、不生成最终话术；LLM 只负责理解、分析、表达和总结，不负责组织事实、权限、Scope、Provider、Capability 或系统事实。

## 3. Foundation Layer

Foundation Layer 是系统元数据与上下文底座，不执行业务。

包含：

```text
Business Domain Taxonomy
Capability Registry
Skill Registry
Provider Registry
Identity & Scope Model
Context Store
Organization Foundation
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

### 3.2 Organization Foundation

Organization Foundation 属于 Foundation Layer，是 AI OS 的唯一组织事实来源。

它不是 People Runtime，不是 Business Domain，也不是 Policy。

Organization Foundation 至少包含：

```text
Organization Directory
Contact Directory
Department Tree
Department Membership
Organization Graph
Role Model
Management Scope
Alias Dictionary
Identity Index
Organization Resolver
Source Metadata
```

定位：

- Contact Directory 只是 Organization Foundation 的一个数据集，不能等同于组织模型。
- Feishu Organization 是官方组织数据来源。
- Organization Foundation 是标准化、增强后的本地组织主数据。
- Role Model、Management Scope、Alias Dictionary 可以由本地维护和人工补充。

同步策略：

```text
Feishu Organization
-> Organization Sync
-> Organization Foundation
```

- Full Sync：组织树、用户、部门、Membership、Leader。
- Incremental Sync：Webhook 处理组织和人员变化。
- On-demand Refresh：Resolver 未命中时刷新单个组织对象。

禁止事项：

- Runtime 禁止每次对话直接扫飞书通讯录。
- Runtime 禁止使用字符串 contains 解析部门、组、事业部或人员。
- LLM 禁止猜测组织对象。
- Policy 禁止直接查询通讯录或自行解析组织关系。

Organization Resolver 输出标准对象：

```text
resolved_user_id
resolved_department_id
resolved_scope
confidence
candidates
reason
```

### 3.3 Capability Registry

统一模型：

```text
Business Domain
-> Capability
-> Skill
-> Provider Registry
```

职责：

- Domain 回答：业务世界怎么分类。
- Capability 回答：业务用户能让数字参谋做什么。
- Skill 回答：系统内部有哪些可治理、可授权、可测试、可审计的原子能力。
- Provider Registry 回答：具体由哪个外部或内部能力实现。

Capability Registry 不是 Observability，也不是 Runtime。它是 System Metadata Foundation，并被以下模块共同使用：

- Command Engine：识别 capability 候选。
- Policy Engine：读取风险、确认、授权要求。
- Runtime Engine：选择 execution strategy 与 provider operation。
- Interaction Layer：展示能力目录、能力清册、治理中心。
- Observability Layer：做缺口诊断和健康检查。

### 3.4 Identity & Scope

企业级 Scope Model：

```text
SELF
USER
TEAM
DEPARTMENT
COMPANY
```

所有 Runtime Context、RuntimeActionInput、RuntimeResult、ResultContext 必须携带 company_id。禁止隐式默认公司。

### 3.5 Context Store

Context Store 不是新的 Engine。它保存对话上下文、状态上下文和跨 Engine 流转所需的上下文事实。

冻结规则：

- 不能因为新增一个 `context` service 文件，就新增架构层。
- Context 可以由多个 Engine 消费，但必须有明确 owner。
- Context 只能提供输入或承载结果，不能自己做业务决策、权限判断或执行动作。
- Profile 可以帮助理解和表达，但不能替代 Policy，也不能扩大查询范围或执行身份。

Context owner / consumer：

| Context | Owner | Consumers | Boundary |
| --- | --- | --- | --- |
| Conversation Context | Command Engine | Command / Conversation LLM / Presentation LLM / Follow-up | 最近对话、非文本消息占位、上一轮业务结果摘要 |
| Profile Context | Cognitive Engine | Command / Policy / Presentation LLM | 用户画像、角色习惯、表达偏好；不得越权 |
| Scope Context | Policy Engine | Command / Runtime / Result Filter | 查询范围与目标对象 |
| Runtime Context | Runtime Engine | Runtime / Provider | 执行身份、company_id、session、permission scope |
| Result Context | Runtime Engine | Command / Conversation LLM / Presentation LLM / Interaction | 上一轮 RuntimeResult 摘要与追问锚点 |
| Resource Context | Policy Engine | Policy / Result Filter | Operational / Cognitive 资源权限元数据 |
| Cognitive Context | Cognitive Engine | Policy / Reasoning LLM / Presentation LLM | Evidence / Snapshot / Insight / MemoryCandidate 的可见认知输入 |

Profile Context 三分法：

| Profile Type | Consumer | Allowed Effect | Forbidden Effect |
| --- | --- | --- | --- |
| Intent Profile | Command Engine | 帮助理解 intent、scope 倾向、业务简称、常用对象 | 不得授权、不得执行、不得绕过 Policy |
| Presentation Profile | Presentation LLM / Interaction | 调整语气、详细程度、格式偏好、管理者视角表达 | 不得改变数量、状态、风险等级、权限结论 |
| Cognitive Profile | Cognitive Engine | 沉淀长期画像和偏好候选 | 不直接参与单次执行，除非被显式提取为 Intent / Presentation Profile |

Profile Update 规则：

- Command LLM 可以输出 `profile_update` 候选，用于记录用户在对话中明确表达出的称呼、语气、详细程度等偏好。
- Profile Update 只能写表达偏好，例如 `preferred_address`、`avoid_direct_name`、`tone_tips`、`style`、`verbosity`。
- Profile Update 禁止写入或修改 role、permission、company_id、department、scope、execution identity。
- 下一轮对话必须同时读取 System Facts 与 Presentation Profile：事实用于准确，画像用于自然表达；表达画像不得覆盖权限事实，权限事实也不得覆盖用户称呼偏好。

UserContextPack 规则：

- `UserContextPack` 不是新 Engine，只是 Command / Presentation 可复用的 LLM 输入包。
- 它统一装配 Identity Facts、Intent Profile、Presentation Profile、Conversation Context 与 Result Context。
- Command LLM 必须优先读取 `UserContextPack`，避免身份事实、画像偏好、上一轮结果和最近对话在多个 prompt 中漂移。
- `UserContextPack` 不做权限判断、不执行动作、不调用 Provider，只提供上下文输入。

标准链路：

```text
User Message
-> Command reads Conversation Context + Intent Profile
-> CommandPlan
-> Policy reads Scope / Resource / Profile boundary
-> Runtime / Cognitive Read
-> Policy Result Filter
-> RuntimeResult writes Result Context
-> Presentation reads Result Context + Presentation Profile
-> InteractionPayload
```

## 4. Core Engines

Core Engines 是系统核心，不直接绑定 UI 或飞书产品。

```text
Command Engine
Policy Engine
Runtime Engine
Cognitive Engine
```

### 4.1 Command Engine

冻结方向：

这不是 Intent Refactor。这是 Conversation First Command Engine Refactor V1。

职责：

- 先构建 `ConversationState`，统一管理 active_domain、active_topic、active_object、active_collection、last_user_goal、last_assistant_question、pending_confirmation、pending_clarification、previous_result_reference、presentation_preference 和 user_profile。
- 运行 Semantic Understanding。LLM 只负责理解当前话语，输出 speech_act、topic、target、operation、requested_output、parameters、confidence、ambiguities。
- 运行 Dialogue Resolver，结合 ConversationState、SemanticFrame、Capability Registry summary 和 deterministic hints 直接输出 `CommandFrame`。
- 输出结构化 `CommandFrame` 与兼容 `CommandPlan`。

允许使用 LLM，但 LLM 只能输出 SemanticFrame，不得输出 Capability、Provider、Runtime、权限、Identity 或 Credential。

冻结链路：

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

Command Engine 的默认路线是 Conversation-first。规则只提供 deterministic hints，不直接抢路由；Capability Registry 不参与理解，只在 Dialogue Resolver 之后被 Runtime/能力解析消费。

Command Engine 禁止：

- 做权限判断。
- 调用 Tool / Provider / Feishu API / MCP / CLI。
- 写 WorkEvent。
- 生成最终业务 Result。
- 绕过 Policy 或 Runtime。
- 让 LLM 直接选择 Provider、Tool、API、credential 或执行身份。
- 新增 `DialogueDecision`、`IntentFrame` 等与 `CommandFrame` 竞争的中间输出。
- 让业务域关键词入口绕过 ConversationState / SemanticFrame / DialogueResolver。

旧对象处理：

- `result_context / pending_action / pending_confirmation / clarification` 暂不删除。
- 它们只能作为 `ConversationStateBuilder` 的输入。
- 其他入口模块不得直接读取这些对象来抢路由。

V1 范围：

- 接入：People、Knowledge。
- 不接入：Task 写动作、Approval 写动作、Mail 写动作、全量 Provider 迁移。
- 后续任何入口 Bug 必须归因到 ConversationState、SemanticFrame、DialogueResolver、Policy 或 Response Orchestrator，不得继续新增业务域关键词入口。

Explicit Command Guard V1：

- 只处理显式 `/...` 控制面命令，不处理普通自然语言。
- 命令族包括：help、session_control、observability、governance、policy。
- 典型命令包括：`/help`、`/reset`、`/system status`、`/system diagnostics`、`/runtime trace`、`/capability registry`、`/policy identity`。
- 未知 `/...` 命令必须返回帮助说明，不进入业务工具、不调用 Provider、不触发 Runtime Action。
- 未来新增硬规则必须先进入显式命令注册表，并声明 command、family、intent、scope、description。

Command LLM Intent V1 规则：

- 显式控制面命令由 Explicit Command Guard 处理，例如 `/system diagnostics`。
- 高置信写入动作可以由确定性 parser 保护，避免 LLM 把动作改路由。
- 其他自然语言默认进入 Command LLM，由 LLM 结合 UserContextPack 和 Capability Summary 判断。
- LLM 只能输出结构化 Intent Candidate、`CommandFrame`、`draft_response_hint` 和安全 `profile_update` 候选，不得执行、不准越过 Validator。
- Validator 只能接受已登记 Runtime strategy、合法 question_type、合法 data_scope。
- 高置信 LLM 候选可补足泛化自然语言查询，例如企业任务负荷、公司日程风险。
- 低置信 LLM 候选只有在给出 missing_params / clarification 时，才能进入引导式对话。
- 未知 intent、低置信且不可追问候选、从 query 升级为 action 的候选必须丢弃。
- 如果 `draft_response_hint` 已通过安全校验，V5 smalltalk 可以直接使用该回复，避免再调用第二次 Conversation LLM 导致延迟和人格漂移。

LLM Routing V1：

- Foreground LLM：`command_intent` / `conversation` / `presentation` 当前走 DeepSeek API，优先保证理解和表达质量。
- Background LLM：`evidence_analysis` / `snapshot_builder` / `insight_generation` / `long_summary` / `reasoning` 继续走 DeepSeek API，用于较重的分析和认知生成。
- 本地模型不作为权限依据，不直接调用 Tool，不改变 Policy / Runtime 边界。
- 当前无 GPU 云服务器上的轻量本地模型不作为前台默认；未来 GPU 推理服务就绪后，可在 Routing Policy 中把前台路由切到本地强模型。

`CommandFrame` 是 Command 输出的统一对话帧，最小字段包括：

```json
{
  "utterance_type": "conversation/business_query/cognitive_query/action_request/system_explanation",
  "dialogue_mode": "answer/present/clarify/execute",
  "user_goal": "",
  "intent": "",
  "domain": "",
  "capability": "",
  "skill_intent": "",
  "scope": "",
  "target": {},
  "params": {},
  "missing_slots": [],
  "context_used": {},
  "response_intent": {},
  "draft_response_hint": "",
  "confidence": 0.0,
  "route_path": ""
}
```

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

- Subject Resolver：从 Organization Foundation 获取 PolicySubject。
- Scope Resolver：从 Organization Foundation 获取 resolved scope 与 management scope。
- Permission Decision：判断是否允许访问或执行。
- Identity Decision：判断 BOT / USER / TENANT 执行身份。
- Visibility Decision：判断字段、来源、引用和敏感信息可见性。
- Confirmation / Authorization：判断是否需要确认或授权。
- Operational Filter：过滤 Task / Approval / Calendar / People / Knowledge / Business 等 operational data。
- Cognitive Filter：过滤 WorkEvent / Evidence / Snapshot / Insight / Memory 等 cognitive data。
- Result Filter：输出 Runtime、Interaction、LLM 可消费的最终过滤结果。

统一链路：

```text
Organization Foundation
-> PolicySubject / ManagementScope
-> Policy Engine
-> Operational Filter
-> Cognitive Filter
-> PolicyFilteredData
-> RuntimeResult
```

核心原则：

- AI OS 只有一套权限系统：Policy Engine。
- Operational Filter 和 Cognitive Filter 都是 Policy Engine 的过滤器，不是独立权限系统。
- Policy 不负责组织解析，不查询飞书通讯录；组织事实全部来自 Organization Foundation。
- Runtime、Cognitive Engine、Interaction、LLM 只能消费 Policy Filter 之后的数据。
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
- 读取 Conversation Context 和 Intent Profile，用于理解多轮上下文、常用简称和范围倾向。
- 读取 Presentation Profile，用于生成自然 `draft_response_hint`，并在用户明确表达偏好时输出 `profile_update` 候选。

禁止：

- 执行动作。
- 选择 Provider / Tool / API。
- 判断权限。
- 决定执行身份。
- 将 Profile 当作授权依据。
- 把称呼、语气等表达偏好升级成身份事实或权限事实。

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
- 读取 Result Context 和 Presentation Profile，生成更自然但不改语义的回答。

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

飞书功能只能映射为 Business Domain Capability、Provider Registry 条目或 Provider Layer 调用。

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

状态定义：

- ACTIVE：仍是当前系统合同的一部分。
- FROZEN：阶段结论或样板已冻结，后续可参考但不继续扩展。
- ARCHIVED：历史文档，已被本文或 ACTIVE 文档吸收。

### 9.1 Active Documents

| Document | Status | Role |
| --- | --- | --- |
| `V5_RUNTIME_CONSTITUTION.md` | ACTIVE | 唯一架构总图 |
| `ARCHITECTURE_INDEX.md` | ACTIVE | 架构文档索引 |
| `ORGANIZATION_FOUNDATION_V1.md` | ACTIVE | Foundation: 组织事实层 |
| `UNIFIED_POLICY_ENGINE_V0.md` | ACTIVE | 唯一权限系统 |
| `CAPABILITY_REGISTRY_MODEL.md` | ACTIVE | Foundation: Domain -> Capability -> Skill -> Provider |
| `ENTERPRISE_COGNITIVE_FOUNDATION_V1.md` | ACTIVE | Cognitive Engine V1 |
| `CURRENT_MISSION.md` | ACTIVE | 当前任务 |

### 9.2 Frozen References

| Document | Status | Role |
| --- | --- | --- |
| `ENTERPRISE_SCOPE_MODEL_REVIEW.md` | FROZEN | Scope Model 评审记录 |
| `EXECUTION_IDENTITY_AUDIT.md` | FROZEN | BOT / USER / TENANT / CLI 身份审计 |
| `EXECUTION_IDENTITY_CONTRACT_DESIGN.md` | FROZEN | 执行身份合同设计 |
| `TASK_RUNTIME_SAMPLE_*` | FROZEN | Task Runtime 样板记录 |
| `APPROVAL_RUNTIME_SAMPLE.md` | FROZEN | Approval Runtime 样板 |
| `APPROVAL_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md` | FROZEN | Approval 样板验收记录 |
| `ECF_V1_FREEZE_REVIEW.md` | FROZEN | Cognitive V1 冻结评审 |
| `SNAPSHOT_TRIGGER_MATRIX.md` | FROZEN | Snapshot 生成节奏规则 |
| `REGISTRY_*` / `CAPABILITY_REGISTRY_*` review docs | FROZEN | Registry 设计和迁移评审记录 |

### 9.3 Archived Documents

| Document | Status | Role |
| --- | --- | --- |
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
| Conversation Context as separate Engine / Layer | Context Store consumed by Command Engine |
| Shared Context Services as architecture layer | Context Store |
| Profile only loaded at output side | Intent Profile + Presentation Profile + Cognitive Profile |
| Tool Layer as architecture layer | Provider Layer |
| Card Policy | Interaction render-only contract |
| Diagnostics as business governance | Observability Layer |
| Feishu product modules | Business Domain + Provider Registry + Provider Layer |
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
-> Provider Registry
```

新增认知能力必须挂到：

```text
WorkEvent
-> Evidence
-> Snapshot
-> Insight
```
