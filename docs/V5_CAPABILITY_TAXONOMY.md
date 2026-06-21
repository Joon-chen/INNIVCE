# V5 Capability Taxonomy

本文档冻结 V5 业务域与能力体系。

核心原则：

```text
Business Domain
-> Capability
-> Skill
-> Provider
```

不要按飞书产品分类。

飞书产品、API、CLI、MCP 都只能作为 Provider 或 Provider Mapping，不能成为系统的一层业务域。

## 1. Final Business Domains

V5 业务域冻结为 7 个：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

### People

组织与人员。

包含：

- 组织架构
- 通讯录
- 员工档案
- 考勤
- 绩效
- 招聘
- 薪资

### Communication

沟通协作。

包含：

- 消息
- 群聊
- 邮件
- 公告
- 机器人

### Workspace

任务、日程与执行。

包含：

- 任务
- 待办
- OKR
- 项目
- 里程碑
- 风险
- 日程
- 会议
- 会议室
- 会议纪要

Calendar 不作为独立业务域，归入 Workspace。

### Process

流程与审批。

包含：

- 审批
- 报销
- 采购
- 付款
- 请假
- 出差
- 合同流转
- 授权确认

Approval 不作为独立业务域，归入 Process。

### Knowledge

知识与文档。

包含：

- 知识库
- Wiki
- 文档
- 文件
- 电子表格
- 会议纪要沉淀

### Business

业务运营。

包含：

- 客户
- 商机
- 订单
- 合同
- 供应商
- 产品
- 工单
- 库存
- 项目

Bitable / Base 只是业务数据载体，不是业务域。

### Intelligence

智能分析。

包含：

- 日报
- 周报
- 会议总结
- 风险分析
- 经营分析
- 决策建议
- 管理洞察

Intelligence 的 Provider 可以来自：

- Internal AI
- WorkEvent
- Evidence
- Snapshot
- MemoryCandidate
- Memory
- Knowledge

## 2. Feishu Mapping

飞书能力映射如下：

| Domain | Feishu Mapping |
| --- | --- |
| People | Contact, Department, Attendance, Payroll, Performance, Recruitment |
| Communication | IM, Group, Mail, Bot, Announcement |
| Workspace | Task, Todo, OKR, Project, Calendar, Meeting, Meeting Room, Minutes |
| Process | Approval, Workflow |
| Knowledge | Wiki, Doc, Drive, Sheet, Minutes |
| Business | Bitable, Base, custom business tables |
| Intelligence | Internal AI, WorkEvent, Evidence, Snapshot, Memory, Knowledge |

说明：

- `Approval` 是 Feishu mapping，不是一级业务域。
- `Calendar` 是 Feishu mapping，不是一级业务域。
- `Bitable` 是业务数据载体，不是业务域。
- `Doc / Wiki / Sheet / Drive` 是知识与数据载体，不直接决定 Capability 分类。

## 3. Domain -> Capability -> Skill -> Provider

统一模型：

```text
Business Domain
业务域：企业工作分类

Capability
业务能力：业务用户能理解的能力

Skill
系统原子能力：可治理、可授权、可测试、可审计

Provider
底层执行来源：Feishu / Internal AI / Memory / WorkEvent / Knowledge / External API
```

示例：

```text
Domain: Process
Capability: 审批通过
Skill: approval_approve
Provider: Feishu Approval Provider

Domain: Workspace
Capability: 创建任务
Skill: task_create
Provider: Feishu Task Provider

Domain: Workspace
Capability: 安排会议
Skill: calendar_create_event
Provider: Feishu Calendar Provider

Domain: Knowledge
Capability: 阅读文档
Skill: document_read
Provider: Feishu Doc Provider

Domain: Intelligence
Capability: 风险分析
Skill: risk_detect
Provider: Internal AI + WorkEvent + Evidence + Memory
```

## 4. Capability Catalog

用途：

```text
面向业务用户。
回答：数字参谋能做什么？
```

展示结构：

```text
Domain
-> Capability
```

禁止展示：

- Skill
- Provider
- CLI
- API
- MCP
- Feishu endpoint
- Runtime operation

示例：

```text
Process
- 查询审批
- 查看审批详情
- 审批通过
- 审批拒绝
- 查询报销依据
- 分析审批风险

Workspace
- 查询任务
- 创建任务
- 更新任务
- 识别延期风险
- 查询日程
- 安排会议
- 总结会议纪要

Knowledge
- 搜索知识
- 阅读文档
- 创建文档
- 总结文档
- 提取行动项

Communication
- 发送消息
- 搜索邮件
- 总结邮件
- 生成回复建议

Intelligence
- 风险分析
- 周报生成
- 管理洞察
- 决策建议
```

## 5. Skill Registry

用途：

```text
面向管理员和开发者。
回答：系统内部有哪些原子能力？
```

展示结构：

```text
Skill
Domain
Capability
Provider
Skill Type
Risk Level
Status
Confirmation Required
Runtime Supported
Result Receipt Supported
Last Health
```

Skill Type：

```text
Provider Skill
AI Skill
Cognitive Skill
```

示例：

```text
Skill: approval_approve
Domain: Process
Capability: 审批通过
Provider: Feishu Approval
Skill Type: Provider Skill
Risk: High
Status: Enabled
Confirmation: Required
Runtime Supported: Yes
Receipt: Yes
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

## 6. Governance Center

用途：

```text
能力治理。
回答：哪些能力不安全、不完整、不可上线？
```

治理视图：

```text
Skill Governance
Provider Governance
Risk Governance
Action Governance
```

重点检查：

- Skill 已登记但无 Provider。
- Skill 已开放但未接 Runtime。
- 高风险 Skill 缺确认。
- 写操作缺回执。
- Provider 异常。
- Provider 未绑定 company_id。
- Action 未走 Runtime。
- Action 缺 confirmation_token。
- Action 失败后无恢复建议。

治理优先级：

```text
P0: 越权、误执行、无确认写操作
P1: 执行失败或状态不闭环
P2: 体验不完整或运维不可见
P3: 元数据不完整
```

## 7. System Diagnostics

用途：

```text
运行时健康检查。
回答：系统能否正常运行？
```

只关注：

- Runtime
- Provider
- Memory
- Action
- Permission
- Result Context
- Response Experience
- Follow-up

禁止展示业务域统计：

- Process 数量
- Workspace 数量
- People 数量
- Knowledge 数量
- Business 数量

这些属于能力目录或管理分析，不属于系统诊断。

## 8. Current V5 Page Migration

页面拆分：

```text
Capability Catalog
面向业务用户

Skill Registry
面向管理员 / 开发者

Governance Center
面向系统负责人

System Diagnostics
面向运维 / 开发
```

迁移步骤：

1. 冻结 Domain 标准枚举。
2. 新增 Capability 标准定义。
3. Skill 必须归属 Domain + Capability。
4. Provider 不再作为一级分类展示。
5. 能力目录改为 Domain -> Capability。
6. 能力清册改为 Skill 维度。
7. 治理中心改成治理问题列表。
8. 系统诊断只保留运行时健康。

## 9. Data Model Suggestions

建议模型：

```text
BusinessDomain
- id
- name
- label
- description
- status

Capability
- id
- domain_id
- name
- label
- description
- user_visible
- status

Skill
- id
- capability_id
- domain_id
- skill_type
- name
- label
- risk_level
- status
- requires_confirmation
- runtime_supported
- receipt_supported

ProviderBinding
- id
- skill_id
- provider_name
- provider_operation
- status
- permission_required
- health_status

GovernanceFinding
- id
- finding_type
- severity
- skill_id
- provider_id
- message
- recommendation
- status
```

Domain 枚举：

```text
people
communication
workspace
process
knowledge
business
intelligence
```

Skill Type 枚举：

```text
provider_skill
ai_skill
cognitive_skill
```

## 10. API Structure Suggestions

业务用户能力目录：

```text
GET /api/v5/domains
GET /api/v5/domains/{domain}/capabilities
GET /api/v5/capabilities/catalog
```

管理员能力清册：

```text
GET /api/v5/skills
GET /api/v5/skills/{skill_id}
GET /api/v5/skills?domain=process
GET /api/v5/skills?domain=workspace
GET /api/v5/skills?risk=high
GET /api/v5/skills?status=enabled
```

Provider 绑定：

```text
GET /api/v5/providers
GET /api/v5/provider-bindings
GET /api/v5/provider-bindings?skill_id=approval_approve
```

治理中心：

```text
GET /api/v5/governance/findings
GET /api/v5/governance/summary
GET /api/v5/governance/priorities
```

系统诊断：

```text
GET /api/v5/diagnostics/runtime
GET /api/v5/diagnostics/providers
GET /api/v5/diagnostics/permissions
GET /api/v5/diagnostics/result-context
GET /api/v5/diagnostics/response-experience
GET /api/v5/diagnostics/follow-up
```

禁止：

```text
GET /api/v5/approval/status
GET /api/v5/task/status
GET /api/v5/wiki/status
```

因为这会把系统重新拖回飞书产品分类。

## 11. Freeze Decision

V5 能力分类冻结为：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

下一阶段 Task Cognitive Sample 必须归属：

```text
Domain: Workspace
```

而不是：

```text
Task Module
```
