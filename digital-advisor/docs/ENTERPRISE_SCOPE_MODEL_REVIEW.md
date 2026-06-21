# Enterprise Scope Model Review

本文档冻结企业级 Scope Model。

本阶段不实现代码，不新增 Runtime State，不修改 Provider。

## 1. Why Scope First

Digital Advisor 不是个人助手。

它是企业数字参谋。

因此 Query 类能力不能默认只回答：

```text
我的任务
我的审批
我的客户
```

还必须统一支持：

```text
指定人员
团队
部门
公司
指定业务对象
```

如果不先冻结 Scope，Task / Approval / People / Business 会各自长出一套范围判断，Command、Policy、Runtime、Provider 边界会再次混乱。

## 2. Enterprise Scope Model

统一 Scope 枚举：

| Scope | Meaning | Example |
| --- | --- | --- |
| `SELF` | 当前用户 | 我的任务、我的审批、我的客户 |
| `USER` | 指定人员 | 张三的任务、李四的客户 |
| `TEAM` | 当前管理团队或指定团队对象 | 我团队的任务、项目 A 任务 |
| `DEPARTMENT` | 指定部门 | 销售部任务、采购部审批 |
| `COMPANY` | 当前公司 | 全公司待审批、所有延期任务 |

Scope 只表达查询范围。

业务对象过滤不新增 Scope 枚举。

例如：

```text
项目 A 任务
```

应表达为：

```text
scope = TEAM or COMPANY
scope_filter.object_type = project
scope_filter.object_id or object_name = 项目A
```

## 3. Scope Data Model

建议冻结最小 Scope Context：

```json
{
  "scope": "SELF|USER|TEAM|DEPARTMENT|COMPANY",
  "company_id": "",
  "target_user_id": "",
  "target_user_text": "",
  "department_id": "",
  "department_text": "",
  "team_id": "",
  "team_text": "",
  "object_type": "",
  "object_id": "",
  "object_text": "",
  "time_range": "",
  "filters": {}
}
```

字段说明：

- `scope`：统一企业查询范围。
- `company_id`：所有 Scope 必须绑定公司。
- `target_user_*`：仅 `USER` 使用。
- `department_*`：仅 `DEPARTMENT` 使用。
- `team_*`：`TEAM` 使用。
- `object_*`：项目、客户分组、审批类型等业务对象过滤。
- `time_range`：本周、超过 3 天、最近 30 天等时间过滤。
- `filters`：风险等级、金额、状态、标签等条件。

现有 `IntentResult.data_scope` 可映射到 Scope，但不应继续作为唯一企业查询范围模型。

## 4. Scope -> Permission Mapping

角色建议：

| Role | SELF | USER | TEAM | DEPARTMENT | COMPANY |
| --- | --- | --- | --- | --- | --- |
| 普通员工 | Allow | Deny by default | Deny by default | Deny by default | Deny by default |
| 团队负责人 | Allow | Allow within team | Allow own team | Deny by default | Deny by default |
| 部门管理者 | Allow | Allow within department | Allow managed team | Allow managed department | Deny by default |
| Admin | Allow | Allow by company policy | Allow | Allow | Allow |
| Owner | Allow | Allow | Allow | Allow | Allow |

原则：

- Policy 决定 Scope 是否允许。
- Command 只识别用户想查什么范围。
- Runtime 只执行已授权的 Scope。
- Provider 不判断企业权限，只接收授权后的查询参数。
- 如果 Scope 超权，RuntimeResult 返回 `denied`，Interaction 只展示结果。

## 5. Task Query Scope Matrix

| User Intent | Scope | Capability | Runtime Strategy | Provider Operation | Scope Context |
| --- | --- | --- | --- | --- | --- |
| 我的任务 | `SELF` | `task_query` | `task_query` | `list_my_tasks` | current user |
| 我的待办 | `SELF` | `task_query` | `task_query` | `list_my_tasks` | current user |
| 张三的任务 | `USER` | `task_query` | `task_query` or future `task_user_query` | `search_tasks` / provider filter | `target_user_text=张三` |
| 销售部任务 | `DEPARTMENT` | `task_query` | future scoped `task_query` | `search_tasks` / data source filter | `department_text=销售部` |
| 项目 A 任务 | `TEAM` | `task_query` | future scoped `task_query` | `search_tasks` / object filter | `object_type=project`, `object_text=项目A` |
| 所有延期任务 | `COMPANY` | `task_delay_risk` | future `task_risk_query` | Snapshot / Task Provider / Evidence | `filters.status=overdue` |
| 所有高风险任务 | `COMPANY` | `task_delay_risk` | future `task_risk_query` | Snapshot / Insight | `filters.risk_level=high` |
| 本周到期任务 | `COMPANY` | `task_query` | future scoped `task_query` | `search_tasks` / provider filter | `time_range=this_week`, `filters.due=true` |

Task V1 只实现 `SELF`。

但 Contract 必须预留 Scope Context，避免把 `task_query` 固化成“我的任务”。

## 6. Approval Query Scope Matrix

| User Intent | Scope | Capability | Runtime Strategy | Provider Operation | Scope Context |
| --- | --- | --- | --- | --- | --- |
| 我的待审批 | `SELF` | `approval_query` | `approval_query` | `list_pending` | current user |
| 张三的审批 | `USER` | `approval_query` | future scoped `approval_query` | provider filter / snapshot query | `target_user_text=张三` |
| 采购部审批 | `DEPARTMENT` | `approval_query` | future scoped `approval_query` | snapshot / provider filter | `department_text=采购部` |
| 全公司待审批 | `COMPANY` | `approval_query` | future `approval_company_query` | snapshot / provider aggregation | company |
| 超过 3 天未处理审批 | `COMPANY` | `approval_query` | future `approval_overdue_query` | snapshot / WorkEvent / provider | `filters.pending_days_gt=3` |
| 高金额审批 | `COMPANY` | `payment_review` or `reimbursement_analysis` | future scoped query | Snapshot / Evidence | `filters.amount_level=high` |

Approval V1 已经验证 `SELF` 主链路。

公司级审批查询不应复用单人待审批 Provider 作为唯一来源，后续应优先从 WorkEvent / Snapshot 聚合。

## 7. People Query Scope Matrix

| User Intent | Scope | Capability | Runtime Strategy | Provider Operation | Scope Context |
| --- | --- | --- | --- | --- | --- |
| 我 | `SELF` | `employee_profile_read` | future `people_self_profile` | People Provider | current user |
| 张三 | `USER` | `contact_lookup` | `people_lookup` | `search_person` | `target_user_text=张三` |
| 销售部人员 | `DEPARTMENT` | `contact_lookup` | `department_members` | `list_department_members` | `department_text=销售部` |
| 研发部人员 | `DEPARTMENT` | `contact_lookup` | `department_members` | `list_department_members` | `department_text=研发部` |
| 全公司人员 | `COMPANY` | `organization_lookup` | `organization_snapshot` | `get_org_snapshot` | company |

People 是 Scope Resolver 的基础数据源，但 People Provider 不等于 USER Resolver。

USER Resolver 仍然是未来独立阶段。

## 8. Business Query Scope Matrix

| User Intent | Scope | Capability | Runtime Strategy | Provider Operation | Scope Context |
| --- | --- | --- | --- | --- | --- |
| 我的客户 | `SELF` | `customer_profile` | future `customer_query` | Business Provider / Bitable | current user |
| 张三客户 | `USER` | `customer_profile` | future `customer_query` | Business Provider / Bitable | `target_user_text=张三` |
| 销售部客户 | `DEPARTMENT` | `customer_profile` | future `customer_query` | Business Provider / Bitable | `department_text=销售部` |
| 全公司客户 | `COMPANY` | `customer_profile` | future `customer_company_query` | Business Provider / Bitable | company |
| 风险客户 | `COMPANY` | `customer_profile` / `risk_detect` | future `customer_risk_query` | Snapshot / Insight | `filters.risk_level=high` |
| 沉默客户 | `COMPANY` | `customer_profile` / `risk_detect` | future `customer_silent_query` | Snapshot / WorkEvent | `filters.activity_gap=true` |
| 重点客户 | `COMPANY` | `customer_profile` | future `customer_key_query` | Business Provider / Snapshot | `filters.segment=key` |

Business 的 Provider 可能是 Bitable，但业务域是 Business。

Bitable 只是载体，不是 Scope。

## 9. RuntimeActionInput Scope

RuntimeActionInput 需要携带 Scope Context。

建议不是只加一个字符串字段，而是加：

```json
{
  "scope_context": {
    "scope": "SELF",
    "company_id": "",
    "object_type": "",
    "object_id": "",
    "filters": {}
  }
}
```

原因：

- Action 可能来自 scoped query 的某个结果。
- Runtime 需要知道动作是从哪个授权范围派生出来的。
- 审计需要记录“从公司级风险任务列表发起完成动作”还是“从我的任务详情发起完成动作”。
- Policy 可二次确认 action scope 是否仍然有效。

但 Scope 不应让 Action 绕过对象级权限。

Action 仍必须携带明确对象：

```text
task_guid
approval_task_id
customer_id
```

## 10. RuntimeResult Scope

RuntimeResult 需要携带 Scope Context。

建议放在：

```json
{
  "metadata": {
    "scope_context": {}
  }
}
```

原因：

- Interaction 需要展示结果范围。
- Follow-up 需要继承上一次查询范围。
- 多轮对话需要区分“继续看我的任务”还是“继续看销售部任务”。
- Result Cache / Snapshot 合并需要知道结果对应哪个 Scope。

Interaction 不解释 Scope 权限，只展示 RuntimeResult 中的 Scope Label。

## 11. Impact On Task Runtime Contract

对当前 Task Runtime Contract 的影响：

| Area | Impact |
| --- | --- |
| `task_query` | 不能永久等同于 `list_my_tasks`，V1 可只支持 `SELF`，但 Contract 要带 `scope_context` |
| `task_complete` | Action 必须带 `scope_context` + `task_guid`，但 Provider 只需要明确 task id |
| Missing Params | `task_guid` 属于 OBJECT，不应在 V1 作为 TEXT missing param 实现 |
| RuntimeResult | `task_list` 必须带 `metadata.scope_context` |
| InteractionPayload | 只展示 Scope Label，不做权限判断 |
| Policy | 必须在 Runtime 执行前判断 Scope 是否授权 |
| Provider | 不承担企业权限判断 |

Task V1 可接受的最小 Scope：

```text
scope = SELF
```

但测试应验证：

```text
metadata.scope_context.scope = SELF
metadata.scope_context.company_id exists
```

## 12. Contract Freeze

冻结结论：

- Query 类能力必须显式携带 Scope。
- Scope 枚举为 `SELF / USER / TEAM / DEPARTMENT / COMPANY`。
- 指定项目、客户分组、审批类型等属于 Scope Filter，不新增 Scope 枚举。
- Policy 是 Scope 权限唯一判断层。
- RuntimeActionInput 需要 `scope_context`。
- RuntimeResult 需要 `metadata.scope_context`。
- Task Runtime Sample 实现前，必须先补 Scope Contract Test。

## 13. Next Step

进入：

```text
Enterprise Scope Contract Test Phase
```

先补最小合同测试：

- `task_query` result metadata includes `scope_context.scope = SELF`。
- `task_query` result metadata includes `scope_context.company_id`。
- `task_complete` action input can carry `scope_context`。
- Permission denies unauthorized `COMPANY` scoped query for ordinary employee。

完成后再回到：

```text
Task Runtime Sample Contract Test Phase
```
