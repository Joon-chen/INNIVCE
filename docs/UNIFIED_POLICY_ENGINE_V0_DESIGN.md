# Unified Policy Engine V0 Design

架构归属：`V5_RUNTIME_CONSTITUTION.md` 中的 Policy Engine。

本文档不是独立架构总图。它只定义 Policy Engine V0 的合同和边界。若本文与 `V5_RUNTIME_CONSTITUTION.md` 冲突，以 Constitution 为准。

本文档冻结 Unified Policy Engine V0 的最小合同。

本阶段不实现完整权限中心，不新增数据库表，不改 Runtime 执行逻辑，不接 OAuth，不做复杂 ACL，也不做权限 UI。

## 0. Engine Boundary

Policy Engine 独立于 Runtime Engine。

V0 可以仍在同一代码仓库、同一进程内实现，但职责必须独立：

```text
Runtime Engine = 状态和执行
Policy Engine = 边界、身份、授权、确认、结果裁剪
```

Policy Engine 横切：

- Operational Data。
- WorkEvent。
- Evidence。
- Snapshot。
- Insight。
- Profile / Style / Preference。
- Action。

## 1. Why Unified Policy

Digital Advisor 的最终结果通常不是单一数据源，而是混合结果：

```text
Operational Data
+ WorkEvent
+ Evidence
+ Snapshot
+ Insight
```

例如审批详情：

```text
Feishu 实时审批数据
+ Snapshot 当前判断
+ Evidence 判断依据
+ Insight 建议动作
```

因此不能把实时数据权限和认知数据权限拆成两个互不相干的系统。权限本质属于 Policy Layer。V0 采用一个 Unified Policy Engine，同时覆盖：

- Operational Data Plane：飞书实时数据与 OA 操作。
- Cognitive Data Plane：WorkEvent / Evidence / Snapshot / Insight / Memory。

统一原则：

```text
最终结果权限 = 实时数据权限 ∩ 认知数据权限 ∩ 当前查询 scope
```

## 2. Runtime Chain

V0 冻结主链路：

```text
Command Plan
-> Policy Preflight
-> Runtime 执行实时/认知读取
-> Policy Result Filter
-> RuntimeResult
-> InteractionPayload
```

Policy 两次介入。

### 2.1 Policy Preflight

Preflight 发生在 Runtime 执行前。它只决定是否允许尝试、用什么身份尝试、是否需要授权或确认；它不是最终展示权限。

Preflight 决定：

- 当前 actor 能查什么 scope。
- 是否允许目标 scope。
- 首选 actor_identity。
- 首选 credential_mode。
- 是否允许 USER fallback。
- 是否允许读取认知数据类型。
- 是否需要确认。
- 是否需要授权。

### 2.2 Policy Result Filter

Result Filter 发生在 Runtime 读取实时数据和认知数据之后、RuntimeResult 生成之前。它决定混合结果最终能展示什么。

Result Filter 裁剪：

- Operational item 是否可见。
- WorkEvent 是否可见。
- Evidence 是否可见。
- Snapshot 是否可见。
- Insight 是否可见。
- 哪些字段需要 redacted。
- 哪些来源不能展示。
- 是否只能展示聚合。
- 是否需要隐藏 reason / source reference。

V0 冻结实现状态：

- `RuntimeResult.metadata.policy_result_filter` 是 Interaction Layer 可见的唯一过滤决策摘要。
- `RuntimeResult.items` 必须使用过滤后的 item 副本。
- `RuntimeResult.actions` 必须基于过滤后的 item 生成，禁止隐藏 item 继续生成 action。
- `resource_plane=cognitive` 是认知层判定的主依据，`resource_type` 只是细分类型。
- Team / Department / Company scope 下的 Cognitive item 默认隐藏来源引用字段，只保留可展示结论。
- Denied resource item 必须在进入 InteractionPayload 前移除。
- Card / Portal / SidePanel 只消费过滤后的 RuntimeResult，不重新解释权限。

## 3. Core Contracts

V0 合同先作为 service-level dataclass / dict 使用。后续可提升为正式 Policy Engine 模型。

### 3.1 PolicySubject

PolicySubject 描述当前发起请求的人。

```json
{
  "actor_user_id": "",
  "actor_open_id": "",
  "company_id": "",
  "role": "member/admin/owner",
  "departments": [],
  "managed_departments": [],
  "is_owner": false,
  "is_admin": false
}
```

字段说明：

- `actor_user_id`：系统内部用户 ID。
- `actor_open_id`：飞书 open_id。
- `company_id`：当前显式公司上下文。
- `role`：当前公司内角色。
- `departments`：actor 所属部门。
- `managed_departments`：actor 管理范围。
- `is_owner` / `is_admin`：管理权限快捷判断，不替代 scope 判断。

### 3.2 PolicyScope

PolicyScope 描述请求范围。

```json
{
  "requested_scope": "self/user/team/department/company",
  "resolved_scope": "self/user/team/department/company",
  "target_user_id": "",
  "target_department_id": "",
  "target_company_id": "",
  "target_group_id": ""
}
```

规则：

- `requested_scope` 来自 Command Layer。
- `resolved_scope` 来自 Policy Preflight。
- 普通员工请求 `company` 可被拒绝，也可被降级为 `self`，具体由 capability policy 决定。
- `target_company_id` 必须明确，不允许隐式默认公司。

### 3.3 PolicyResource

PolicyResource 描述一个待访问资源。Operational 与 Cognitive 资源使用同一合同。

```json
{
  "resource_plane": "operational/cognitive",
  "resource_type": "task/calendar/approval/workevent/evidence/snapshot/insight/memory",
  "source_system": "feishu/internal",
  "source_object_type": "",
  "source_object_id": "",
  "company_id": "",
  "owner_user_id": "",
  "owner_department_id": "",
  "visibility_scope": "self/team/department/company",
  "allowed_user_ids": [],
  "allowed_departments": [],
  "allowed_roles": [],
  "data_classification": "normal/sensitive/confidential"
}
```

Operational resource examples:

- Feishu approval detail。
- Feishu task。
- Feishu calendar event。
- Feishu mail message。

Cognitive resource examples:

- WorkEvent: `approval_created`。
- Evidence: approval expense evidence。
- Snapshot: approval cognitive state。
- Insight: approval risk recommendation。

V0 字段约定：

- Operational resource 必须标注 `resource_plane=operational`。
- Cognitive resource 必须标注 `resource_plane=cognitive`。
- Cognitive resource 必须尽量携带 `source_event_ids` / `source_object_type` / `source_object_id`。
- Cognitive resource 的 `visibility_scope` 默认继承来源对象权限。
- `inherited_visibility_scope` 用于说明认知资源继承的来源可见范围。
- 细分类型可以是 `approval_snapshot`、`approval_evidence`、`customer_insight` 等；Policy 判断不能依赖固定枚举。

### 3.4 IdentityDecision

IdentityDecision 描述 Runtime 应用什么身份尝试访问或执行。

```json
{
  "actor_identity": "BOT/USER/ADMIN/SYSTEM",
  "credential_mode": "TENANT_TOKEN/USER_TOKEN/ADMIN_SESSION/INTERNAL/CLI_PROFILE",
  "allows_fallback": false,
  "requires_authorization": false,
  "authorization_status": "AUTHORIZED/MISSING_AUTHORIZATION/UNKNOWN"
}
```

Query 身份原则：

```text
All Query = BOT/TENANT first
```

USER_TOKEN 只能作为受控 fallback 或代表当前用户执行动作的身份，不能作为 Query 默认入口。

如果某个企业范围 Query 尚未接入 BOT/TENANT 主路径，系统必须明确返回“企业实时读取能力未授权/未接入”。不得偷偷改用当前用户 USER_TOKEN、本地认知数据或缓存伪装为企业实时读取结果。

### 3.5 PolicyDecision

PolicyDecision 是 Preflight 的输出。

```json
{
  "allowed": true,
  "reason": "",
  "resolved_scope": "self",
  "identity_decision": {},
  "allowed_resource_types": [],
  "denied_resource_types": [],
  "requires_confirmation": false,
  "requires_authorization": false
}
```

语义：

- `allowed=false`：Runtime 不应执行。
- `allowed=true`：Runtime 可按 `identity_decision` 尝试读取或执行。
- `allowed_resource_types` / `denied_resource_types` 用于混合读取，例如允许 operational approval detail，但不允许 evidence detail。

### 3.6 ResultFilterDecision

ResultFilterDecision 是 Result Filter 对单个资源或结果 section 的裁剪输出。

```json
{
  "visible": true,
  "redacted_fields": [],
  "hidden_sections": [],
  "aggregation_only": false,
  "reason_hidden": false,
  "source_reference_visible": true
}
```

语义：

- `visible=false`：资源整体不可展示。
- `redacted_fields`：字段级脱敏。
- `hidden_sections`：隐藏 Evidence / reason / source reference 等 section。
- `aggregation_only=true`：只允许展示聚合，不允许展示明细。
- `reason_hidden=true`：AI 原因不可展示或只能展示脱敏原因。
- `source_reference_visible=false`：不可暴露来源对象 ID、附件、原文引用。

## 4. Permission Principles

V0 冻结以下原则。

1. 最终结果权限 = 实时数据权限 ∩ 认知数据权限 ∩ 当前查询 scope。
2. 认知层不能比来源对象更开放。
3. WorkEvent / Evidence 默认继承来源对象权限。
4. Snapshot 可以展示摘要，但不能暴露无权限来源细节。
5. Insight 可以聚合放大到管理视角，但必须脱敏，不得暴露无权限来源对象。
6. User Token 不能成为认知层越权依据。
7. BOT/TENANT 用于企业范围 Query，不能偷偷用当前用户 USER_TOKEN 代表全公司查询。
8. USER_TOKEN 只用于当前用户个人资源 fallback 或代表当前用户执行的动作。
9. 指定人员 Query 不得默认用当前用户 USER_TOKEN 代查。
10. Owner/Admin 可以看更大范围，但必须在明确 company_id / group context 下。

## 5. Query Identity Policy

Query 统一 BOT first：

```text
Query
-> BOT/TENANT attempt
-> 如果 Bot 明确 unsupported / permission_denied / not_visible
-> Policy 判断是否允许 USER_TOKEN fallback
-> 已授权则 User fallback
-> 未授权则 WAITING_AUTHORIZATION
```

Scope 规则：

| Scope | First identity | USER fallback |
| --- | --- | --- |
| SELF | BOT/TENANT | 允许，但仅限当前用户个人资源且已授权 |
| USER | BOT/TENANT | 不允许使用当前用户 token 代查；需要目标用户授权或管理员委托 |
| TEAM | BOT/TENANT | 默认不允许普通 USER fallback |
| DEPARTMENT | BOT/TENANT | 默认不允许普通 USER fallback |
| COMPANY | BOT/TENANT | 不允许普通 USER fallback |

Action / Write 规则：

| Action Type | Preferred identity |
| --- | --- |
| 当前用户个人动作 | USER_TOKEN |
| 企业 Bot 可代执行动作 | TENANT_TOKEN |
| 高风险动作 | 由 Policy 要求确认 |
| 管理动作 | ADMIN_SESSION 或明确 Owner/Admin policy |

CLI_PROFILE 不属于生产身份策略，只能作为 dev/debug adapter 或迁移期显式工具适配器。

## 6. Mixed Result Filtering

混合结果必须在 RuntimeResult 生成前统一裁剪。

示例结构：

```json
{
  "operational": {},
  "snapshot": {},
  "evidence": [],
  "insights": []
}
```

Result Filter 应逐项生成 `ResultFilterDecision`：

- operational item 可见，但 evidence 细节不可见：展示实时对象，隐藏证据细节。
- snapshot 可见但 source references 不可见：展示摘要，不展示附件或来源 ID。
- insight 聚合可见但明细不可见：展示趋势和建议，不展示个人对象。

推荐展示策略：

- 可见：正常展示。
- Redacted：展示字段名和“已脱敏”。
- Hidden：不展示 section。
- Aggregation-only：展示聚合统计，不展示来源对象。

## 7. Scenario Review

### 7.1 员工查看自己的审批详情

输入：

```text
Operational: approval detail
Cognitive: snapshot + evidence + insight
Scope: SELF
```

Preflight：

- 允许 SELF。
- Query first identity = BOT/TENANT。
- 若飞书审批详情需要个人身份且 Bot 不可读，可允许当前用户 USER_TOKEN fallback。
- 允许读取相关 Snapshot / Insight。
- Evidence 读取需继承审批来源权限。

Result Filter：

- 员工可看自己的审批详情。
- 可看与该审批直接相关的 Snapshot 和 AI 建议。
- Evidence 可展示与本人审批相关的判断依据。
- 不展示其他审批或他人附件来源。

预期：

可看自己的详情和相关 AI 建议。

### 7.2 部门主管查看部门审批风险

输入：

```text
Operational: department approval list
Cognitive: department risk insight
Scope: DEPARTMENT
```

Preflight：

- 仅当 target_department_id 在 `managed_departments` 内时允许。
- First identity = BOT/TENANT。
- 不允许使用主管 USER_TOKEN 代查全部门。
- 允许读取部门级 Insight。

Result Filter：

- 部门聚合 Insight 可见。
- 主管有权看的审批明细可见。
- 无权明细隐藏或脱敏。
- Evidence 细节仅对有来源对象权限的项目展示。

预期：

可看部门聚合和自己有权看的明细；无权明细需要脱敏。

### 7.3 Owner 查看公司审批风险趋势

输入：

```text
Operational: company approval statistics
Cognitive: company insight
Scope: COMPANY
```

Preflight：

- Owner/Admin 在明确 company_id 下允许 COMPANY。
- First identity = BOT/TENANT。
- 不需要普通 USER_TOKEN。
- 允许公司级 Insight。

Result Filter：

- 公司级聚合趋势可见。
- 原始附件、个人隐私字段、无必要来源明细默认隐藏。
- 如需展开明细，逐对象再做 ResultFilterDecision。

预期：

可看公司级聚合，不需要暴露所有原始附件。

### 7.4 员工查询全公司风险

输入：

```text
Scope: COMPANY
Actor: member
```

Preflight：

- 默认拒绝，或降级到 SELF。
- 如果降级，`resolved_scope=self`，并在 reason 中记录 `scope_degraded`。
- 不允许用当前用户 USER_TOKEN 查询公司风险。

Result Filter：

- 如果拒绝，无结果。
- 如果降级，只返回 actor 自己范围内可见的风险。

预期：

Policy Preflight 拒绝或降级到 SELF。

### 7.5 AI 建议引用了无权限 Evidence

输入：

```text
Insight references Evidence that actor cannot access.
```

Preflight：

- 可能允许读取 Insight。
- 不代表允许读取 Evidence 细节。

Result Filter：

- Insight 可展示脱敏建议。
- 隐藏 Evidence 详情。
- `reason_hidden=true` 或 `source_reference_visible=false`。
- 如需要原因，展示抽象描述，例如“部分证据不在当前权限范围内”。

预期：

隐藏 Evidence 细节，只显示脱敏原因或不显示原因。

## 8. Field Placement

### 8.1 Fields For Runtime Context

可先挂在 Runtime Context / session context：

- `PolicySubject.actor_user_id`
- `PolicySubject.actor_open_id`
- `PolicySubject.company_id`
- `PolicySubject.role`
- `PolicySubject.departments`
- `PolicySubject.managed_departments`
- `PolicyScope.requested_scope`
- `PolicyScope.resolved_scope`
- `PolicyScope.target_user_id`
- `PolicyScope.target_department_id`
- `PolicyScope.target_company_id`
- `IdentityDecision`

### 8.2 Fields For WorkEvent / Snapshot / Insight

WorkEvent 已适合承载：

- `company_id`
- `source`
- `source_type`
- `object_type`
- `object_id`
- `actor`
- `visibility_scope`
- `allowed_user_ids`
- `allowed_departments`
- `allowed_roles`
- `data_classification`
- `payload`

Snapshot / Insight V0 可先在 `payload` 或 metadata 中承载：

- `source_system`
- `source_object_type`
- `source_object_id`
- `visibility_scope`
- `allowed_user_ids`
- `allowed_departments`
- `allowed_roles`
- `data_classification`
- `source_event_ids`
- `source_reference_policy`

后续如果 Insight 独立持久化，再提升为正式字段。

### 8.3 Service-Level Filter First

V0 先用 service-level filter 实现：

- `build_policy_subject(context)`
- `resolve_policy_scope(plan, subject)`
- `decide_query_identity(scope, resource_type, subject)`
- `filter_runtime_result_sections(result, subject, scope)`
- `redact_policy_resource(resource, decision)`

不新增数据库表，不做完整 ACL DSL。

### 8.4 Later Policy Engine

后续再实现：

- 统一 Policy Engine 服务。
- PolicyDecision 审计。
- Resource permission index。
- Insight aggregation policy。
- Delegated admin policy。
- Target user authorization policy。
- Policy UI。

## 9. Required Answers

### 9.1 实时数据和认知数据如何统一判断权限

统一用 `PolicySubject + PolicyScope + PolicyResource` 判断。Operational 与 Cognitive 资源都转成 PolicyResource。最终展示前使用 Result Filter 取交集并裁剪。

### 9.2 Policy Preflight 和 Result Filter 分别做什么

Preflight 决定是否允许尝试读取/执行、用什么身份、是否需要授权或确认。Result Filter 决定混合结果最终展示哪些对象、字段、原因和来源。

### 9.3 Query 身份如何选择 BOT/TENANT 或 USER_TOKEN

所有 Query 默认 BOT/TENANT first。USER_TOKEN 只能作为受控 fallback：仅限当前用户个人资源、Bot 明确不可读、Policy 允许、用户已授权。团队/部门/公司 Query 不允许用当前用户 USER_TOKEN 代查。

### 9.4 混合结果如何裁剪

对每个 Operational item、WorkEvent、Evidence、Snapshot、Insight 生成 ResultFilterDecision。不可见则隐藏；部分可见则 redacted；管理聚合可见但明细不可见时使用 aggregation_only。

### 9.5 Insight 聚合如何脱敏

Insight 可以放大到管理视角，但必须重新标注 visibility_scope 和 data_classification。聚合 Insight 不得暴露无权限来源对象、附件、个人隐私字段或 source reference。

### 9.6 为什么 User Token 不能成为认知层越权依据

User Token 只证明某个用户可访问自己的实时资源或可代表自己执行动作。它不能扩大认知层数据的可见范围，也不能用来绕过来源对象权限、公司边界或管理 scope。认知层权限必须继承来源权限，并由 Policy Result Filter 统一裁剪。

## 10. V0 Freeze

Unified Policy Engine V0 冻结为：

```text
One Policy Engine
Two data planes
Two intervention points
One mixed-result filter
```

不要再建立单独的 Cognitive Permission System。认知权限是 Policy Engine 对 Cognitive Data Plane 的资源策略。

## 11. Implementation Planning

V0 实施目标不是一次性实现完整权限中心，而是把现有 `permission.py` / `policy_layer.py` 收敛成可验证的 service-level Policy 主链路。

第一条样板选择 Workspace，原因：

- Task / Calendar 已经暴露出 Query identity、USER fallback、企业范围读取和个人动作写入的真实边界。
- Workspace 同时覆盖 Query 和 Action，能验证 Policy Preflight。
- 后续 Workspace Cognitive Sample 会混合 Operational Data + Cognitive Data，能验证 Result Filter。

### 11.1 Phase 1: Policy Preflight Contract

目标：

```text
CommandPlan
-> Policy Preflight
-> PermissionDecision metadata
-> Runtime
```

最小实现：

- 保留现有 `PermissionDecision`，不新增数据库表。
- 在 `permission.py` 中显式输出：
  - `policy_subject`
  - `policy_scope`
  - `identity_decision`
  - `allowed_resource_types`
  - `denied_resource_types`
- `policy_subject` 先从 `RuntimeContext.identity` 构造。
- `policy_scope` 先从 `IntentResult.data_scope` 和 `RuntimeScope` 构造。
- `identity_decision` 先复用现有 BOT-first query 逻辑。

验收：

- Query 默认 `BOT / TENANT_TOKEN`。
- SELF Query 可以标记 `user_fallback_allowed=true`。
- COMPANY / DEPARTMENT / TEAM Query 不允许普通 USER fallback。
- Action 继续按 Runtime 确认策略进入 USER 或 TENANT 执行身份。

### 11.2 Phase 2: Workspace Query Policy Sample

目标：

```text
task_query / calendar_query
-> scope
-> identity decision
-> capability availability
```

最小规则：

- `scope=self`：BOT/TENANT first；如果 Bot 不支持个人私有资源，可进入 USER fallback。
- `scope=company`：只能 BOT/TENANT；未接入时返回企业实时读取能力未授权/未接入。
- `scope=user`：不得用当前用户 USER_TOKEN 代查目标用户。
- `scope=department/team`：必须基于管理范围；V0 可先拒绝或返回未接入。

验收：

- “查看我的任务 / 日程”：走 BOT first，不默认 USER。
- “查看全公司任务 / 日程”：若企业 Provider 未接入，明确返回未接入，不查本地认知数据替代。
- “查看张三任务 / 日程”：不使用当前用户 token 代查。

### 11.3 Phase 3: Result Filter Skeleton

目标：

```text
Operational Data
+ Cognitive Data
-> Policy Result Filter
-> RuntimeResult
```

最小实现：

- 先不做完整 ACL。
- 在 RuntimeResult metadata 中保留 `policy_filter` 摘要。
- 对每个 result section 标记：
  - visible
  - redacted_fields
  - hidden_sections
  - aggregation_only
  - source_reference_visible
- V0 先用于 Workspace 样板；Approval 后续复用。

验收：

- RuntimeResult 只能包含 Policy 允许展示的内容。
- Snapshot / Evidence / Insight 不得比来源 Operational Object 更开放。
- 管理聚合可以展示，但来源明细必须按权限裁剪。

### 11.4 Phase 4: Test Guard

新增 Contract Test：

- Query identity BOT-first。
- USER fallback 只允许 SELF personal resource。
- company / department / team 不允许当前用户 USER_TOKEN fallback。
- enterprise Provider 未接入时不得 fallback 到本地认知数据。
- RuntimeResult 包含 `policy_filter` 摘要。

暂不实现：

- Policy UI。
- ACL DSL。
- Resource permission index。
- Delegated admin policy。
- Target user authorization flow。
- Insight Store。
