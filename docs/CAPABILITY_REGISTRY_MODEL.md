# Capability Registry Model

本文档冻结 V5 Capability Registry 设计。

Domain 已冻结为：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

本阶段不再调整 Domain。

## 1. Registry Purpose

Capability Registry 负责承接统一能力模型：

```text
Domain
-> Capability
-> Skill
-> Provider
```

职责边界：

- Domain 回答：属于哪类企业工作。
- Capability 回答：业务用户能让数字参谋做什么。
- Skill 回答：系统内部有哪些可治理、可授权、可测试、可审计的原子能力。
- Provider 回答：能力由哪个底层系统、AI 能力或数据来源支撑。

禁止重新建立：

- Approval Module
- Task Module
- Calendar Module
- Wiki Module
- Doc Module
- Mail Module
- Bitable Module

飞书产品只能作为 Provider 或 Provider Mapping。

## 2. Model Freeze

### Capability

建议字段：

```text
id
domain
name
label
description
user_visible
capability_type
lifecycle_status
risk_level
primary_entry
supported_surfaces
related_skills
provider_dependencies
```

说明：

- `id` 使用稳定英文标识，例如 `approval_query`。
- `domain` 必须属于已冻结 Domain。
- `user_visible` 决定是否进入能力目录。
- `supported_surfaces` 记录 Bot / Detail / Portal / Admin 等入口。
- `related_skills` 只引用 Skill，不直接引用 Provider operation。

### Skill

建议字段：

```text
id
domain
capability_id
name
label
skill_type
risk_level
status
requires_confirmation
runtime_supported
receipt_supported
provider_bindings
```

Skill Type：

```text
provider_skill
ai_skill
cognitive_skill
```

Skill 是治理、授权、确认、执行闭环的基本对象。

### Provider Binding

建议字段：

```text
provider_id
provider_name
provider_operation
skill_id
status
permission_required
health_status
last_checked_at
```

Provider Binding 只描述技术绑定，不决定业务分类。

## 3. Domain -> Capability Mapping

### People

| Capability ID | Capability |
| --- | --- |
| organization_lookup | 查询组织架构 |
| contact_lookup | 查询通讯录 |
| employee_profile_read | 查看员工档案 |
| attendance_query | 查询考勤 |
| performance_context | 查看绩效上下文 |
| recruitment_context | 查看招聘上下文 |
| payroll_context | 查看薪资上下文 |

### Communication

| Capability ID | Capability |
| --- | --- |
| message_send | 发送消息 |
| chat_search | 搜索群聊 |
| mail_search | 搜索邮件 |
| mail_summary | 总结邮件 |
| mail_reply_draft | 生成邮件回复建议 |
| announcement_read | 查看公告 |
| bot_interaction | 机器人交互 |

### Workspace

| Capability ID | Capability |
| --- | --- |
| task_query | 查询任务 |
| task_create | 创建任务 |
| task_update | 更新任务 |
| task_follow_up | 跟进任务 |
| task_delay_risk | 识别任务延期风险 |
| okr_query | 查询 OKR |
| project_status | 查看项目状态 |
| calendar_query | 查询日程 |
| meeting_schedule | 安排会议 |
| meeting_summary | 总结会议 |
| meeting_action_items | 提取会议行动项 |

Calendar 属于 Workspace Capability，不作为独立 Domain。

### Process

| Capability ID | Capability |
| --- | --- |
| approval_query | 查询审批 |
| approval_detail | 查看审批详情 |
| approval_approve | 审批通过 |
| approval_reject | 审批拒绝 |
| approval_evidence_review | 查看审批判断依据 |
| reimbursement_analysis | 分析报销 |
| procurement_review | 采购审核 |
| payment_review | 付款审核 |
| leave_review | 请假审核 |
| travel_review | 出差审核 |

Approval 属于 Process Capability，不作为独立 Domain。

### Knowledge

| Capability ID | Capability |
| --- | --- |
| knowledge_search | 搜索知识 |
| document_read | 阅读文档 |
| document_create | 创建文档 |
| document_summary | 总结文档 |
| file_lookup | 查找文件 |
| sheet_read | 读取表格 |
| sheet_update | 更新表格 |
| minutes_read | 阅读会议纪要 |

### Business

| Capability ID | Capability |
| --- | --- |
| customer_profile | 查看客户画像 |
| opportunity_tracking | 跟进商机 |
| order_query | 查询订单 |
| contract_review | 合同审核 |
| supplier_review | 供应商审核 |
| product_lookup | 查询产品 |
| ticket_tracking | 跟进工单 |
| inventory_query | 查询库存 |
| project_business_context | 查看业务项目上下文 |

Bitable / Base 只是 Business 数据载体。

### Intelligence

| Capability ID | Capability |
| --- | --- |
| daily_report_generate | 生成日报 |
| weekly_report_generate | 生成周报 |
| risk_detect | 识别风险 |
| business_analysis | 经营分析 |
| decision_recommendation | 决策建议 |
| management_insight | 管理洞察 |
| cognitive_snapshot_read | 读取认知快照 |
| insight_generate | 生成 Insight |

当前 V1 不实现 Insight Engine 或 Insight Store，`insight_generate` 只作为未来能力登记边界。

## 4. Capability -> Skill Mapping

第一批映射样板：

| Domain | Capability | Skill | Provider |
| --- | --- | --- | --- |
| Process | approval_query | approval_query_pending | Feishu Approval |
| Process | approval_detail | approval_detail_read | Feishu Approval |
| Process | approval_approve | approval_approve | Feishu Approval |
| Process | approval_reject | approval_reject | Feishu Approval |
| Process | approval_evidence_review | approval_evidence_read | Evidence |
| Process | approval_evidence_review | approval_snapshot_read | Snapshot |
| Process | reimbursement_analysis | approval_expense_evidence_build | Internal AI + Evidence |
| Workspace | task_query | task_query | Feishu Task |
| Workspace | task_create | task_create | Feishu Task |
| Workspace | task_update | task_update | Feishu Task |
| Workspace | calendar_query | calendar_query | Feishu Calendar |
| Workspace | meeting_schedule | calendar_create_event | Feishu Calendar |
| Workspace | meeting_summary | meeting_summary_generate | Minutes + Internal AI |
| Knowledge | knowledge_search | knowledge_search | Feishu Wiki / Knowledge |
| Knowledge | document_read | document_read | Feishu Doc |
| Knowledge | document_summary | document_summary_generate | Feishu Doc + Internal AI |
| Communication | message_send | message_send | Feishu IM |
| Communication | mail_search | mail_search | Feishu Mail |
| Communication | mail_summary | mail_summary_generate | Feishu Mail + Internal AI |
| Intelligence | risk_detect | risk_detect | WorkEvent + Evidence + Internal AI |
| Intelligence | management_insight | management_insight_generate | Snapshot + Evidence + Internal AI |

规则：

- 一个 Capability 可以关联多个 Skill。
- 一个 Skill 只能归属一个主 Capability。
- 一个 Skill 可以绑定多个 Provider，但必须有主 Provider Binding。
- 高风险写操作 Skill 必须走 Runtime。

## 5. Capability Catalog Design

用途：

```text
面向业务用户。
回答：数字参谋能做什么？
```

展示对象：

```text
Domain
-> Capability
```

每个 Capability 展示：

```text
名称
说明
状态
可用入口
业务价值
```

禁止展示：

- Skill
- Provider
- CLI
- API
- MCP
- Feishu endpoint
- Runtime operation

## 6. Skill Registry Design

用途：

```text
面向管理员和开发者。
回答：系统内部有哪些原子能力？
```

展示对象：

```text
Skill
```

每个 Skill 展示：

```text
Skill ID
Domain
Capability
Skill Type
Provider
Status
Risk Level
Confirmation Required
Runtime Supported
Receipt Supported
Health
```

统计：

- 已登记
- 已开放
- 待开放
- 高风险
- 缺 Provider
- 缺确认
- 缺回执
- Provider 异常

## 7. Governance Center Design

治理对象：

```text
Capability
Skill
Provider
```

### Capability Governance

检查：

- Capability 已展示但没有可用 Skill。
- Capability 已开放但没有入口。
- 高风险 Capability 缺 Policy 说明。
- Capability 所属 Domain 不合法。

### Skill Governance

检查：

- Skill 已登记但无 Provider。
- Skill 已开放但未接 Runtime。
- 高风险 Skill 缺确认。
- 写操作 Skill 缺回执。
- Skill 缺 company_id 上下文要求。

### Provider Governance

检查：

- Provider 异常。
- Provider 权限失效。
- Provider operation 未绑定 Skill。
- Provider 返回结构不满足 Result Contract。
- Provider 未明确 company_id 传递方式。

治理优先级：

```text
P0: 越权、误执行、无确认写操作
P1: 执行失败或状态不闭环
P2: 体验不完整或运维不可见
P3: 元数据不完整
```

## 8. System Diagnostics Boundary

系统诊断保持 Runtime 视角，不参与能力分类。

只检查：

- Runtime
- Provider
- Permission
- Result Context
- Response Experience
- Follow-up
- Action State

禁止按业务域展示诊断：

- Process 健康度
- Workspace 健康度
- People 健康度
- Knowledge 健康度
- Business 健康度

这些属于能力目录、治理中心或管理分析，不属于系统诊断。

## 9. Current Page Migration

页面目标：

| Page | Display Object | Audience |
| --- | --- | --- |
| Capability Catalog | Capability | 业务用户 |
| Skill Registry | Skill | 管理员 / 开发者 |
| Governance Center | Capability / Skill / Provider Finding | 系统负责人 |
| System Diagnostics | Runtime Health | 运维 / 开发 |

迁移原则：

- 能力目录以 Capability 为展示对象。
- 能力清册以 Skill 为展示对象。
- 治理中心以 Capability / Skill / Provider 为治理对象。
- 系统诊断只保留运行时健康，不按业务域分类。

## 10. API Shape Suggestions

Capability Registry：

```text
GET /api/v5/capability-registry/domains
GET /api/v5/capability-registry/capabilities
GET /api/v5/capability-registry/capabilities?domain=workspace
GET /api/v5/capability-registry/skills
GET /api/v5/capability-registry/skills?capability_id=task_query
GET /api/v5/capability-registry/provider-bindings
```

Governance：

```text
GET /api/v5/governance/findings?scope=capability
GET /api/v5/governance/findings?scope=skill
GET /api/v5/governance/findings?scope=provider
```

Diagnostics 保持独立：

```text
GET /api/v5/diagnostics/runtime
GET /api/v5/diagnostics/providers
GET /api/v5/diagnostics/result-context
```

## 11. Forbidden Scope

本阶段禁止：

- 修改 Domain。
- 实现数据库表。
- 实现 API。
- 实现页面迁移。
- 实现 Task 业务代码。
- 新增 Tool。
- 新增 Runtime State。
- 新增 Resolver。
- 新增 Insight Store。
- 新增 Engine。

## 12. Freeze Decision

Capability Model 冻结为：

```text
Domain
-> Capability
-> Skill
-> Provider
```

页面冻结为：

```text
Capability Catalog: Capability
Skill Registry: Skill
Governance Center: Capability / Skill / Provider
System Diagnostics: Runtime
```

下一阶段可以进入：

```text
Task Cognitive Sample Selection
```

Task 必须归属：

```text
Domain: Workspace
Capability: task_query / task_create / task_update / task_follow_up / task_delay_risk
```

而不是：

```text
Task Module
```
